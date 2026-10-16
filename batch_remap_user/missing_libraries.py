# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover all missing linked libraries using one explicitly chosen blend file."""

import os
from collections import defaultdict

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import id_collections, library_key, library_path, path_key


def missing_libraries():
    return [library for library in bpy.data.libraries
            if library.packed_file is None
            and not getattr(library, 'is_archive', False)
            and (library.is_missing or not os.path.isfile(library_path(library)))]


def missing_library_records():
    # Relocate can invalidate all linked RNA wrappers. Retain only text.
    return sorted(({
        'key': library_key(library),
        'path': library_path(library),
        'relative': library.filepath.startswith('//'),
        'indirect': library.parent is not None,
        'parent': library_path(library.parent) if library.parent is not None else '',
    } for library in missing_libraries()), key=lambda record: record['indirect'])


def linked_names(library_keys):
    result = {key: defaultdict(set) for key in library_keys}
    for collection, _ in id_collections():
        for data in getattr(bpy.data, collection):
            if data.library is not None:
                key = library_key(data.library)
                if key in result:
                    result[key][collection].add(data.name)
    return result


def find_missing_linked_libraries(filepath):
    """Relocate missing libraries to this blend, preserving unmatched references."""
    filepath = os.path.realpath(bpy.path.abspath(filepath)) if filepath else ''
    if not filepath.lower().endswith('.blend') or not os.path.isfile(filepath):
        raise ValueError('Choose an existing .blend file')
    target_key = path_key(filepath)
    if bpy.data.filepath and target_key == path_key(bpy.data.filepath):
        raise ValueError('Choose an external blend file, not the current file')
    # Read names only. A file containing no matching IDs must not create a link.
    with bpy.data.libraries.load(filepath, link=True) as (available, requested):
        names = {collection: set(getattr(available, collection, ()))
                 for collection, _ in id_collections()}

    relocated = []
    failures = []
    attempted = set()
    while True:
        # Recovering a parent can also recover or reveal indirect libraries.
        current_records = missing_library_records()
        requirements = linked_names({record['key'] for record in current_records})
        record = next((record for record in current_records if record['key'] not in attempted), None)
        if record is None:
            break
        attempted.add(record['key'])
        if record['indirect']:
            failures.append((record['path'], 'Indirect library; repair the link in ' + record['parent']))
            continue
        if not any(values.intersection(names.get(collection, set()))
                   for collection, values in requirements[record['key']].items()):
            continue
        library = next((lib for lib in bpy.data.libraries if library_key(lib) == record['key']), None)
        if library is None:
            continue
        library_name = library.name
        try:
            result = bpy.ops.wm.lib_relocate(
                'EXEC_DEFAULT', library=library_name,
                directory=os.path.dirname(filepath) + os.sep,
                filename=os.path.basename(filepath),
                relative_path=record['relative'] and bool(bpy.data.filepath),
            )
            target_library = next((lib for lib in bpy.data.libraries if library_key(lib) == target_key), None)
            if result != {'FINISHED'} or target_library is None or target_library.is_missing:
                raise RuntimeError('Blender could not recover the library from this file')
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append((record['path'], str(exc)))
        else:
            relocated.append((record['path'], filepath))

    remaining = [(record['path'], 'Required names were not found or relocation failed')
                 for record in missing_library_records()]
    missing_ids = [(data.name, library_path(data.library))
                   for collection, _ in id_collections()
                   if collection != 'libraries' for data in getattr(bpy.data, collection)
                   if data.is_missing and data.library is not None]
    return relocated, remaining, failures, missing_ids


class FILE_OT_find_missing_linked_libraries(bpy.types.Operator, ImportHelper):
    bl_idname = 'file.find_missing_linked_libraries'
    bl_label = 'Find Missing Linked Libraries'
    bl_description = 'Recover all missing linked libraries from a chosen blend file, without selecting data-blocks'
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.blend'
    filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.libraries)

    def invoke(self, context, event):
        if not missing_libraries():
            self.report({'INFO'}, 'No missing linked libraries')
            return {'CANCELLED'}
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.label(text='Recover all missing libraries from the chosen blend file.')
        self.layout.label(text='Data not found in this file keeps its original link.')

    def execute(self, context):
        try:
            relocated, remaining, failures, missing_ids = find_missing_linked_libraries(self.filepath)
        except (OSError, RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            # Native relocation may have partially completed; preserve Undo.
            return {'FINISHED'}
        for old_path, new_path in relocated:
            print(f'[Find Missing Linked Libraries] {old_path} -> {new_path}')
        for path, reason in remaining + failures:
            print(f'[Find Missing Linked Libraries] {path}: {reason}')
        for name, path in missing_ids:
            print(f'[Find Missing Linked Libraries] Unresolved: {name} ({path})')
        severity = {'WARNING'} if remaining or failures or missing_ids else {'INFO'}
        self.report(severity, f'Libraries processed: {len(relocated)}, unresolved libraries: {len(remaining)}, missing data-blocks: {len(missing_ids)}')
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


def draw_external_data_menu(self, context):
    self.layout.separator()
    self.layout.operator_context = 'INVOKE_DEFAULT'
    self.layout.operator(FILE_OT_find_missing_linked_libraries.bl_idname, text='Find Missing Linked Libraries...')

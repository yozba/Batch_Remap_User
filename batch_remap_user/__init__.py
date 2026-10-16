# SPDX-License-Identifier: GPL-3.0-or-later
"""Remap explicitly selected Outliner IDs to a linked library by exact name."""

import json
import os
from collections import defaultdict

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


BROWSE_TARGET = '__CHOOSE_BLEND_FILE__'


def library_path(library):
    """Filesystem/display path, preserving the blend filename's letter case."""
    return os.path.realpath(bpy.path.abspath(library.filepath))


def path_key(path):
    """Normalize case only for filesystem identity comparisons."""
    return os.path.normcase(os.path.realpath(path))


def library_key(library):
    return path_key(library_path(library))


def id_collections():
    """Discover top-level ID collections, including types added by Blender."""
    for prop in bpy.data.bl_rna.properties:
        if prop.type != 'COLLECTION':
            continue
        cls = getattr(bpy.types, prop.fixed_type.identifier, None)
        if cls is not None and issubclass(cls, bpy.types.ID):
            yield prop.identifier, cls


def collection_name(data):
    for name, cls in id_collections():
        if isinstance(data, cls):
            return name
    return None


def source_problem(data):
    if isinstance(data, (bpy.types.Library, bpy.types.WindowManager,
                         bpy.types.Screen, bpy.types.WorkSpace)):
        return 'Unsupported data-block type'
    if data.is_embedded_data:
        return 'Embedded data-block'
    if data.override_library is not None:
        return 'Library override'
    # Missing linked IDs are valid sources: remapping their users to a healthy
    # ID from another blend file is how broken links can be recovered.
    if isinstance(data, bpy.types.Object) and data.mode != 'OBJECT':
        return 'Object is not in Object Mode'
    if getattr(data, 'is_editmode', False):
        return 'Data-block is in Edit Mode'
    if not collection_name(data):
        return 'No top-level data collection'
    return None


def snapshot_selection(context):
    # Store identities as text: PointerProperties would themselves be remapped.
    result = []
    seen = set()
    selected = tuple(getattr(context, 'selected_ids', ()))
    # A context menu may expose only the clicked ID after changing regions.
    if not selected:
        clicked = getattr(context, 'id', None)
        if isinstance(clicked, bpy.types.ID):
            selected = (clicked,)
    for data in selected:
        if not isinstance(data, bpy.types.ID) or data.as_pointer() in seen:
            continue
        seen.add(data.as_pointer())
        result.append({
            'collection': collection_name(data),
            'name': data.name,
            'library': library_path(data.library) if data.library else '',
            'problem': source_problem(data),
        })
    return result


def resolve_source(record):
    if not record['collection']:
        return None
    source_key = path_key(record['library']) if record['library'] else ''
    for data in getattr(bpy.data, record['collection']):
        key = library_key(data.library) if data.library else ''
        if data.name == record['name'] and key == source_key:
            return data
    return None


def find_target(source, collection, target_path):
    target_key = path_key(target_path)
    for data in getattr(bpy.data, collection):
        if (data.library and library_key(data.library) == target_key
                and data.name == source.name
                and data.bl_rna.identifier == source.bl_rna.identifier
                and not data.is_embedded_data
                and not getattr(data, 'is_missing', False)):
            return data
    return None


def remap_collection_users(source, target):
    # Blender's ID remapper can hang when both collections are children of the
    # same parent. Keep the existing destination link instead of creating a
    # duplicate, then remap the remaining users (instances, modifiers, etc.).
    parents = list(bpy.data.collections) + [scene.collection for scene in bpy.data.scenes]
    duplicates = [parent for parent in parents
                  if source in parent.children.values() and target in parent.children.values()]
    if any(parent.library or parent.override_library for parent in duplicates):
        raise ValueError('Source and target share a parent in linked or overridden data')
    # Replacing a child of the destination with the destination creates a cycle.
    pending = [target]
    visited = set()
    while pending:
        parent = pending.pop()
        if parent == source:
            raise ValueError('Target collection contains the source collection')
        if parent.as_pointer() not in visited:
            visited.add(parent.as_pointer())
            pending.extend(parent.children)
    detached = []
    try:
        for parent in duplicates:
            parent.children.unlink(source)
            detached.append(parent)
        source.user_remap(target)
    except (RuntimeError, TypeError, ValueError, ReferenceError):
        for parent in detached:
            if source not in parent.children.values():
                parent.children.link(source)
        raise


def remap_batch(records, target_path, link_missing=True):
    """Return (remapped, skipped, failures, linked). Never delete source IDs."""
    if not target_path:
        raise ValueError('Choose a blend file')
    target_path = os.path.realpath(bpy.path.abspath(target_path))
    target_key = path_key(target_path)
    if bpy.data.filepath and target_key == path_key(bpy.data.filepath):
        raise ValueError('Choose an external blend file, not the current file')
    target_library = next((lib for lib in bpy.data.libraries
                           if library_key(lib) == target_key), None)
    if target_library is not None:
        # Use the original filename case even for old/scripted normalized paths.
        target_path = library_path(target_library)
    elif not link_missing:
        raise ValueError('Enable Link Missing Matching Data for a file that is not linked yet')
    elif not target_path.lower().endswith('.blend') or not os.path.isfile(target_path):
        raise ValueError('Choose an existing .blend file: ' + target_path)

    sources = []
    skipped = []
    for record in records:
        source = resolve_source(record)
        problem = source_problem(source) if source is not None else 'Source no longer exists'
        if problem:
            skipped.append((record['name'], problem))
        elif source.library and library_key(source.library) == target_key:
            skipped.append((source.name, 'Already in the target library'))
        else:
            sources.append((source, record['collection']))

    linked = 0
    if link_missing:
        missing = defaultdict(set)
        for source, collection in sources:
            if find_target(source, collection, target_path) is None:
                missing[collection].add(source.name)
        if missing:
            if not os.path.isfile(target_path):
                raise ValueError('The target blend file cannot be found: ' + target_path)
            # Inspect the file and request only exact matches, grouped by ID type.
            with bpy.data.libraries.load(target_path, link=True) as (available, requested):
                requested_types = []
                for collection, names in missing.items():
                    if not hasattr(available, collection):
                        continue
                    matches = sorted(names.intersection(getattr(available, collection)))
                    if matches:
                        setattr(requested, collection, matches)
                        requested_types.append(collection)
            linked = sum(data is not None for collection in requested_types
                         for data in getattr(requested, collection))

    # Resolve every destination before changing any references.
    pairs = []
    for source, collection in sources:
        target = find_target(source, collection, target_path)
        if target is None:
            skipped.append((source.name, 'No exact name/type match in the target library'))
        else:
            pairs.append((source, target, source.name))

    remapped = 0
    failures = []
    for source, target, name in pairs:
        try:
            if isinstance(source, bpy.types.Collection):
                remap_collection_users(source, target)
            else:
                source.user_remap(target)
        except (RuntimeError, TypeError, ValueError, ReferenceError) as exc:
            failures.append((name, str(exc)))
        else:
            remapped += 1
    return remapped, skipped, failures, linked


_library_item_cache = {}
_library_enum_items = ()
_browse_item = (BROWSE_TARGET, 'Choose Blend File...',
                'Browse for a blend file and link matching data for the first time', 0)


def library_items(self, context):
    # Retain all strings and stable numeric IDs, also for execute/redo instances.
    global _library_enum_items
    paths = sorted({library_path(lib) for lib in bpy.data.libraries})
    for path in paths:
        if path not in _library_item_cache:
            _library_item_cache[path] = (
                path, os.path.basename(path) + '  —  ' + path, path,
                len(_library_item_cache) + 1,
            )
    _library_enum_items = tuple(_library_item_cache[path] for path in paths) + (_browse_item,)
    return _library_enum_items


def target_library_changed(self, context):
    self.target_path = '' if self.target_library == BROWSE_TARGET else self.target_library


def execute_remap(operator, context, target_path, records, link_missing):
    if not records:
        operator.report({'ERROR'}, 'No data-blocks selected in the Outliner')
        return {'CANCELLED'}
    try:
        remapped, skipped, failures, linked = remap_batch(records, target_path, link_missing)
    except (OSError, RuntimeError, ValueError) as exc:
        operator.report({'ERROR'}, str(exc))
        # A failed library load may have modified Main: keep it undoable.
        return {'FINISHED'}
    for name, reason in skipped + failures:
        print(f'[Batch Remap User] {name}: {reason}')
    severity = {'WARNING'} if skipped or failures else {'INFO'}
    operator.report(severity, f'Remapped: {remapped}, linked: {linked}, skipped: {len(skipped)}, failed: {len(failures)}')
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return {'FINISHED'}


class OUTLINER_OT_batch_remap_user_file(bpy.types.Operator, ImportHelper):
    """Own the selection snapshot while the Outliner becomes a file browser."""
    bl_idname = 'outliner.batch_remap_user_file'
    bl_label = 'Batch Remap User'
    bl_description = 'Choose a blend file, link exact matches, and remap the selected data-blocks'
    bl_options = {'UNDO', 'INTERNAL'}

    filename_ext = '.blend'
    filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})
    selection_json: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        records = json.loads(self.selection_json or '[]')
        return execute_remap(self, context, self.filepath, records, True)


class OUTLINER_OT_batch_remap_user(bpy.types.Operator):
    bl_idname = 'outliner.batch_remap_user'
    bl_label = 'Batch Remap User'
    bl_description = 'Remap selected data-blocks to exact name/type matches in a linked blend file'
    bl_options = {'REGISTER', 'UNDO'}

    target_library: EnumProperty(
        name='Target Blend File', items=library_items, update=target_library_changed,
    )
    target_path: StringProperty(
        name='Blend File Path',
        description='Path to the destination blend file; enter a path or use Browse',
        options={'SKIP_SAVE'},
    )
    selection_json: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    link_missing: BoolProperty(
        name='Link Missing Matching Data', default=True,
        description='Link exact matches from the target file if they are not loaded yet',
    )

    @classmethod
    def poll(cls, context):
        if not context.area or context.area.type != 'OUTLINER':
            cls.poll_message_set('Run from the Outliner')
            return False
        if not getattr(context, 'selected_ids', ()) and not isinstance(getattr(context, 'id', None), bpy.types.ID):
            cls.poll_message_set('Select data-blocks in the Outliner')
            return False
        return True

    def invoke(self, context, event):
        # The right-click menu snapshots its Outliner context before the popup
        # changes regions. F3 invocation captures the current selection here.
        records = json.loads(self.selection_json) if self.selection_json else snapshot_selection(context)
        # Re-check the current IDs instead of trusting an old menu snapshot's
        # diagnostic (for example, one made before missing sources were allowed).
        for record in records:
            source = resolve_source(record)
            record['problem'] = source_problem(source) if source is not None else 'Source no longer exists'
        if not any(not record['problem'] for record in records):
            details = '; '.join(f"{record['name']}: {record['problem']}" for record in records)
            self.report({'WARNING'}, details or 'No data-blocks selected in the Outliner')
            return {'CANCELLED'}
        self.selection_json = json.dumps(records)
        self.target_path = ''
        paths = [item[0] for item in library_items(self, context) if item[0] != BROWSE_TARGET]
        # Prefer a library different from all selected source libraries.
        source_paths = {path_key(record['library']) for record in records if record['library']}
        self.target_library = next((path for path in paths if path_key(path) not in source_paths),
                                   BROWSE_TARGET)
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target_library')
        if self.target_library == BROWSE_TARGET:
            layout.prop(self, 'target_path')
            row = layout.row()
            row.operator_context = 'INVOKE_DEFAULT'
            browse = row.operator(OUTLINER_OT_batch_remap_user_file.bl_idname, text='Browse...')
            browse.selection_json = self.selection_json
            if self.target_path:
                browse.filepath = bpy.path.abspath(self.target_path)
            layout.label(text='Enter a path and press OK, or browse for a blend file.')
        else:
            layout.prop(self, 'link_missing')
        records = json.loads(self.selection_json or '[]')
        layout.label(text=f'Selected: {len(records)} data-block(s)')
        layout.label(text='Exact names and types only. All users of each selected ID are remapped.', icon='INFO')
        layout.label(text='Source data-blocks are kept. Undo: Ctrl+Z.')

    def execute(self, context):
        # Enum changes update this path; also accept a path for scripted calls.
        target_path = self.target_path or self.target_library
        records = json.loads(self.selection_json) if self.selection_json else snapshot_selection(context)
        if target_path == BROWSE_TARGET:
            bpy.ops.outliner.batch_remap_user_file(
                'INVOKE_DEFAULT', selection_json=json.dumps(records),
            )
            # This operator has made no changes. The file browser owns the undo.
            return {'CANCELLED'}
        if not target_path:
            self.report({'ERROR'}, 'Choose a blend file')
            return {'CANCELLED'}
        self.target_path = target_path
        link_missing = True if self.target_library == BROWSE_TARGET else self.link_missing
        return execute_remap(self, context, target_path, records, link_missing)


def draw_menu(self, context):
    if context.space_data and context.space_data.type == 'OUTLINER':
        self.layout.separator()
        self.layout.operator_context = 'INVOKE_DEFAULT'
        operator = self.layout.operator(OUTLINER_OT_batch_remap_user.bl_idname)
        operator.selection_json = json.dumps(snapshot_selection(context))


# ID Data is used for meshes/materials; object/collection have separate menus.
MENU_NAMES = ('OUTLINER_MT_id_data', 'OUTLINER_MT_object',
              'OUTLINER_MT_collection', 'OUTLINER_MT_context_menu')


def register():
    bpy.utils.register_class(OUTLINER_OT_batch_remap_user_file)
    bpy.utils.register_class(OUTLINER_OT_batch_remap_user)
    for name in MENU_NAMES:
        getattr(bpy.types, name).append(draw_menu)


def unregister():
    global _library_enum_items
    for name in reversed(MENU_NAMES):
        getattr(bpy.types, name).remove(draw_menu)
    bpy.utils.unregister_class(OUTLINER_OT_batch_remap_user)
    bpy.utils.unregister_class(OUTLINER_OT_batch_remap_user_file)
    _library_enum_items = ()
    _library_item_cache.clear()


if __name__ == '__main__':
    register()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Remap explicitly selected Outliner IDs to a linked library by exact name."""

import json
import os
from collections import defaultdict

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty


def library_path(library):
    return os.path.normcase(os.path.realpath(bpy.path.abspath(library.filepath)))


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
    if getattr(data, 'is_missing', False):
        return 'Missing library data-block'
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
    for data in getattr(bpy.data, record['collection']):
        path = library_path(data.library) if data.library else ''
        if data.name == record['name'] and path == record['library']:
            return data
    return None


def find_target(source, collection, target_path):
    for data in getattr(bpy.data, collection):
        if (data.library and library_path(data.library) == target_path
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
    if not any(library_path(lib) == target_path for lib in bpy.data.libraries):
        raise ValueError('The target must be an already linked library')

    sources = []
    skipped = []
    for record in records:
        source = resolve_source(record)
        problem = record['problem'] or (source_problem(source) if source else 'Source no longer exists')
        if problem:
            skipped.append((record['name'], problem))
        elif source.library and library_path(source.library) == target_path:
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


def library_items(self, context):
    # Retain all strings and stable numeric IDs, also for execute/redo instances.
    global _library_enum_items
    paths = sorted({library_path(lib) for lib in bpy.data.libraries})
    for path in paths:
        if path not in _library_item_cache:
            _library_item_cache[path] = (
                path, os.path.basename(path) + '  —  ' + path, path,
                len(_library_item_cache),
            )
    _library_enum_items = tuple(_library_item_cache[path] for path in paths)
    return _library_enum_items


def target_library_changed(self, context):
    self.target_path = self.target_library


class OUTLINER_OT_batch_remap_user(bpy.types.Operator):
    bl_idname = 'outliner.batch_remap_user'
    bl_label = 'Batch Remap User'
    bl_description = 'Remap selected data-blocks to exact name/type matches in a linked blend file'
    bl_options = {'REGISTER', 'UNDO'}

    target_library: EnumProperty(
        name='Target Blend File', items=library_items, update=target_library_changed,
    )
    target_path: StringProperty(options={'HIDDEN'})
    selection_json: StringProperty(options={'HIDDEN'})
    link_missing: BoolProperty(
        name='Link Missing Matching Data', default=True,
        description='Link exact matches from the target file if they are not loaded yet',
    )

    @classmethod
    def poll(cls, context):
        if not context.area or context.area.type != 'OUTLINER':
            cls.poll_message_set('Run from the Outliner')
            return False
        # LIBRARIES is the API enum for the UI's "Blender File" mode.
        if context.space_data.display_mode != 'LIBRARIES':
            cls.poll_message_set('Switch the Outliner to Blender File')
            return False
        if not bpy.data.libraries:
            cls.poll_message_set('Link an external blend file first')
            return False
        if not getattr(context, 'selected_ids', ()) and not isinstance(getattr(context, 'id', None), bpy.types.ID):
            cls.poll_message_set('Select data-blocks in the Outliner')
            return False
        return True

    def invoke(self, context, event):
        # The right-click menu snapshots its Outliner context before the popup
        # changes regions. F3 invocation captures the current selection here.
        records = json.loads(self.selection_json) if self.selection_json else snapshot_selection(context)
        if not any(not record['problem'] for record in records):
            details = '; '.join(f"{record['name']}: {record['problem']}" for record in records)
            self.report({'WARNING'}, details or 'No data-blocks selected in the Outliner')
            return {'CANCELLED'}
        self.selection_json = json.dumps(records)
        self.target_path = ''
        paths = [item[0] for item in library_items(self, context)]
        # Prefer a library different from all selected source libraries.
        source_paths = {record['library'] for record in records}
        self.target_library = next((path for path in paths if path not in source_paths), paths[0])
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target_library')
        layout.prop(self, 'link_missing')
        records = json.loads(self.selection_json or '[]')
        layout.label(text=f'Selected: {len(records)} data-block(s)')
        layout.label(text='Exact names and types only. All users of each selected ID are remapped.', icon='INFO')
        layout.label(text='Source data-blocks are kept. Undo: Ctrl+Z.')

    def execute(self, context):
        # Enum changes update this path; also accept a path for scripted calls.
        target_path = self.target_path or self.target_library
        if not target_path:
            self.report({'ERROR'}, 'Choose a linked blend file')
            return {'CANCELLED'}
        self.target_path = target_path
        records = json.loads(self.selection_json) if self.selection_json else snapshot_selection(context)
        try:
            remapped, skipped, failures, linked = remap_batch(records, target_path, self.link_missing)
        except (OSError, RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            # A failed library load may have modified Main: keep it undoable.
            return {'FINISHED'}
        for name, reason in skipped + failures:
            print(f'[Batch Remap User] {name}: {reason}')
        severity = {'WARNING'} if skipped or failures else {'INFO'}
        self.report(severity, f'Remapped: {remapped}, linked: {linked}, skipped: {len(skipped)}, failed: {len(failures)}')
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


def draw_menu(self, context):
    if context.space_data and context.space_data.type == 'OUTLINER' and context.space_data.display_mode == 'LIBRARIES':
        self.layout.separator()
        self.layout.operator_context = 'INVOKE_DEFAULT'
        operator = self.layout.operator(OUTLINER_OT_batch_remap_user.bl_idname)
        operator.selection_json = json.dumps(snapshot_selection(context))


# ID Data is used for meshes/materials; object/collection have separate menus.
MENU_NAMES = ('OUTLINER_MT_id_data', 'OUTLINER_MT_object',
              'OUTLINER_MT_collection', 'OUTLINER_MT_context_menu')


def register():
    bpy.utils.register_class(OUTLINER_OT_batch_remap_user)
    for name in MENU_NAMES:
        getattr(bpy.types, name).append(draw_menu)


def unregister():
    global _library_enum_items
    for name in reversed(MENU_NAMES):
        getattr(bpy.types, name).remove(draw_menu)
    bpy.utils.unregister_class(OUTLINER_OT_batch_remap_user)
    _library_enum_items = ()
    _library_item_cache.clear()


if __name__ == '__main__':
    register()

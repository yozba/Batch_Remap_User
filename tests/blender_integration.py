"""Run with Blender --background --factory-startup --python-exit-code 1."""
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import sys
import shutil
import uuid
import json
import faulthandler

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_remap_user as addon


def snapshot(*ids):
    return addon.snapshot_selection(SimpleNamespace(selected_ids=ids))


def link(path, **groups):
    with bpy.data.libraries.load(str(path), link=True) as (_, requested):
        for group, names in groups.items():
            setattr(requested, group, names)
    return requested


def material_user(name, material):
    mesh = bpy.data.meshes.new(name + 'Mesh')
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def local_material(name):
    return bpy.data.materials.new(name)


@contextmanager
def test_directory(root):
    # mkdtemp's mode=0700 is incompatible with some managed Windows sandboxes.
    directory = root / ('blender-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o777)
    try:
        yield directory
    finally:
        resolved = directory.resolve()
        assert resolved.is_relative_to(root.resolve()) and resolved != root.resolve()
        shutil.rmtree(resolved)


def check():
    addon.register()
    workspace = Path(__file__).resolve().parents[1]
    test_root = (workspace / '.test-output').resolve()
    assert test_root.is_relative_to(workspace)
    test_root.mkdir(exist_ok=True)
    with test_directory(test_root) as directory:
        directory = Path(directory)
        assert directory.resolve().is_relative_to(test_root)
        destination = directory / 'destination.blend'
        other = directory / 'other.blend'
        materials = [local_material(name) for name in ('Anchor', 'Wood', 'Metal', 'Shared', 'ObjectTarget')]
        mesh = bpy.data.meshes.new('Wood')  # Same name as a material: keep types separate.
        target_object = bpy.data.objects.new('ObjectTarget', mesh)
        target_collection = bpy.data.collections.new('CollectionTarget')
        target_collection.objects.link(target_object)
        empty_target_collection = bpy.data.collections.new('LinkedCollectionTarget')
        tree = bpy.data.node_groups.new('NodeTarget', 'GeometryNodeTree')
        bpy.data.libraries.write(str(destination), set(materials + [mesh, tree, target_object, target_collection, empty_target_collection]))
        other_material = local_material('Elsewhere')
        bpy.data.libraries.write(str(other), {other_material})
        bpy.ops.wm.read_factory_settings(use_empty=True)

        # Establish two pre-linked libraries; matching data is initially absent.
        loaded = link(destination, materials=['Anchor'])
        anchor = loaded.materials[0]
        other_loaded = link(other, materials=['Elsewhere']).materials[0]
        path = addon.library_path(anchor.library)
        wood = local_material('Wood')
        metal = local_material('Metal')
        unselected = local_material('Shared')
        suffix = local_material('Wood.001')
        missing = local_material('Absent')
        wood_user = material_user('WoodUser', wood)
        shared_user = material_user('AlsoUsesWood', wood)
        metal_user = material_user('MetalUser', metal)
        unselected_user = material_user('UnselectedUser', unselected)
        suffix_user = material_user('SuffixUser', suffix)
        missing_user = material_user('MissingUser', missing)
        source_mesh = bpy.data.meshes.new('Wood')
        mesh_user = bpy.data.objects.new('MeshUser', source_mesh)
        bpy.context.scene.collection.objects.link(mesh_user)

        counts = addon.remap_batch(snapshot(wood), path, link_missing=False)
        assert counts[0] == 0 and len(counts[1]) == 1 and counts[3] == 0, counts
        assert wood_user.data.materials[0] == wood

        records = snapshot(wood, metal, source_mesh, suffix, missing, anchor, anchor.library, wood)
        assert len(records) == 7, records  # Duplicate selections are processed once.
        counts = addon.remap_batch(records, path)
        assert counts[0] == 3 and len(counts[1]) == 4 and not counts[2] and counts[3] == 3, counts
        target_wood = addon.find_target(wood, 'materials', path)
        assert wood_user.data.materials[0] == target_wood
        assert shared_user.data.materials[0] == target_wood
        assert metal_user.data.materials[0].library == anchor.library
        assert mesh_user.data.library == anchor.library
        assert mesh_user.data.bl_rna.identifier == 'Mesh'
        assert unselected_user.data.materials[0] == unselected
        assert suffix_user.data.materials[0] == suffix
        assert missing_user.data.materials[0] == missing
        assert wood.name == 'Wood' and wood.library is None  # Source is retained.
        assert other_loaded.library != anchor.library

        # A linked source in a different library can remap its local users.
        linked_wood = link(destination, materials=['Shared']).materials[0]
        third_path = directory / 'third.blend'
        bpy.data.libraries.write(str(third_path), {unselected})
        third_wood = link(third_path, materials=['Shared']).materials[0]
        linked_user = material_user('LinkedSourceUser', third_wood)
        counts = addon.remap_batch(snapshot(third_wood), path)
        assert counts[0] == 1 and linked_user.data.materials[0] == linked_wood, counts

        # Node groups need the same concrete tree type.
        shader_tree = bpy.data.node_groups.new('NodeTarget', 'ShaderNodeTree')
        counts = addon.remap_batch(snapshot(shader_tree), path)
        assert counts[0] == 0 and len(counts[1]) == 1, counts
        geometry_tree = bpy.data.node_groups.new('NodeTarget', 'GeometryNodeTree')
        # Shader and geometry nodes share a local namespace, so restore the exact name.
        shader_tree.name = 'ShaderOnly'
        geometry_tree.name = 'NodeTarget'
        counts = addon.remap_batch(snapshot(geometry_tree), path)
        assert counts[0] == 1 and not counts[2], counts

        # Objects and collections are remapped as IDs, using their actual users.
        source_object = bpy.data.objects.new('ObjectTarget', None)
        source_collection = bpy.data.collections.new('CollectionTarget')
        bpy.context.scene.collection.children.link(source_collection)
        source_collection.objects.link(source_object)
        object_user = bpy.data.objects.new('ConstraintUser', None)
        bpy.context.scene.collection.objects.link(object_user)
        constraint = object_user.constraints.new('COPY_LOCATION')
        constraint.target = source_object
        counts = addon.remap_batch(snapshot(source_object, source_collection), path)
        assert counts[0] == 2 and not counts[2], counts
        assert constraint.target.library == anchor.library
        assert bpy.context.scene.collection.children.get('CollectionTarget').library == anchor.library

        # A Collection linked from another blend is a supported source.
        # Both scene links and collection instances remap.
        collection_source_path = directory / 'collection_source.blend'
        link_source_collection = bpy.data.collections.new('LinkedCollectionTarget')
        collection_member = bpy.data.objects.new('LinkedCollectionMember', None)
        link_source_collection.objects.link(collection_member)
        bpy.data.libraries.write(str(collection_source_path), {link_source_collection})
        linked_collection = link(collection_source_path, collections=['LinkedCollectionTarget']).collections[0]
        assert addon.source_problem(linked_collection) is None
        assert snapshot(linked_collection)[0]['problem'] is None
        clicked_only = SimpleNamespace(selected_ids=(), id=linked_collection)
        assert addon.snapshot_selection(clicked_only) == snapshot(linked_collection)
        # Opening a popup can lose the menu's selection context. The captured
        # linked Collection must still reach invoke without the generic warning.
        warnings = []
        invocation = SimpleNamespace(
            selection_json=json.dumps(snapshot(linked_collection)),
            report=lambda severity, message: warnings.append(message),
        )
        menu_context = SimpleNamespace(
            selected_ids=[anchor.library],
            window_manager=SimpleNamespace(invoke_props_dialog=lambda operator, width: {'RUNNING_MODAL'}),
        )
        assert addon.OUTLINER_OT_batch_remap_user.invoke(invocation, menu_context, None) == {'RUNNING_MODAL'}
        assert not warnings
        assert json.loads(invocation.selection_json)[0]['collection'] == 'collections'
        bpy.context.scene.collection.children.link(linked_collection)
        instance = bpy.data.objects.new('LinkedCollectionInstance', None)
        instance.instance_type = 'COLLECTION'
        instance.instance_collection = linked_collection
        bpy.context.scene.collection.objects.link(instance)
        counts = addon.remap_batch(snapshot(linked_collection), path)
        assert counts[0] == 1 and not counts[1] and not counts[2], counts
        assert instance.instance_collection.library == anchor.library
        assert linked_collection not in bpy.context.scene.collection.children.values()

        # Regression: source and destination already share the same scene parent
        # and a linked object. The raw ID remapper hangs on this graph.
        shared_source_path = directory / 'shared_collection_source.blend'
        bpy.data.libraries.write(str(shared_source_path), {source_collection})
        shared_source = link(shared_source_path, collections=['CollectionTarget']).collections[0]
        shared_target = addon.find_target(shared_source, 'collections', path)
        bpy.context.scene.collection.children.link(shared_source)
        shared_instance = bpy.data.objects.new('SharedCollectionInstance', None)
        shared_instance.instance_type = 'COLLECTION'
        shared_instance.instance_collection = shared_source
        bpy.context.scene.collection.objects.link(shared_instance)
        faulthandler.dump_traceback_later(15, exit=True)
        counts = addon.remap_batch(snapshot(shared_source), path)
        faulthandler.cancel_dump_traceback_later()
        assert counts[0] == 1 and not counts[1] and not counts[2], counts
        assert shared_instance.instance_collection == shared_target
        assert shared_source not in bpy.context.scene.collection.children.values()
        assert sum(child == shared_target for child in bpy.context.scene.collection.children) == 1

        # Library overrides are intentionally excluded.
        override = target_wood.override_create(remap_local_usages=False)
        assert addon.source_problem(override) == 'Library override'
        counts = addon.remap_batch(snapshot(override), path)
        assert counts[0] == 0 and len(counts[1]) == 1, counts

        # Test real operator invocation, Outliner poll, and Undo with a stored snapshot.
        area = next(area for area in bpy.context.screen.areas if area.type == 'OUTLINER')
        area.spaces.active.display_mode = 'LIBRARIES'
        operator_source = shared_source
        shared_instance.instance_collection = operator_source
        bpy.context.scene.collection.children.link(operator_source)
        operator_records = snapshot(operator_source)
        bpy.context.preferences.edit.use_global_undo = True
        with bpy.context.temp_override(area=area, selected_ids=[operator_source]):
            assert addon.OUTLINER_OT_batch_remap_user.poll(bpy.context)
            bpy.ops.ed.undo_push(message='Before Batch Remap')
            result = bpy.ops.outliner.batch_remap_user(
                'EXEC_DEFAULT', target_library=path,
                selection_json=json.dumps(operator_records),
            )
            assert result == {'FINISHED'}, result
            assert shared_instance.instance_collection == shared_target
            assert operator_source not in bpy.context.scene.collection.children.values()
            # In background mode Blender needs an explicit post-operation undo step.
            bpy.ops.ed.undo_push(message='After Batch Remap')
            bpy.ops.ed.undo()
            restored_user = bpy.data.objects['SharedCollectionInstance']
            restored_source = addon.resolve_source(operator_records[0])
            assert restored_user.instance_collection == restored_source
            assert restored_source in bpy.context.scene.collection.children.values()

        # A disconnected target must fail before altering users.
        try:
            addon.remap_batch([], str(directory / 'not_linked.blend'))
        except ValueError:
            pass
        else:
            raise AssertionError('Unlinked target was accepted')
    addon.unregister()
    print('PASS: exact matches, mixed types, missing links, shared users, linked sources, objects, collections, overrides, operator, Undo')


check()

"""Run with Blender --background --factory-startup --python-exit-code 1."""
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import sys
import shutil
import uuid
import json
import faulthandler
import os

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


def check_first_link(destination):
    assert not bpy.data.libraries
    source = bpy.data.collections.new('CollectionTarget')
    bpy.context.scene.collection.children.link(source)
    instance = bpy.data.objects.new('FirstLinkInstance', None)
    instance.instance_type = 'COLLECTION'
    instance.instance_collection = source
    bpy.context.scene.collection.objects.link(instance)
    untouched = local_material('Metal')
    records = snapshot(source)
    area = next(area for area in bpy.context.screen.areas if area.type == 'OUTLINER')
    area.spaces.active.display_mode = 'VIEW_LAYER'

    # With no existing libraries, the main dialog offers the file browser.
    reports = []
    invocation = SimpleNamespace(selection_json='', report=lambda severity, message: reports.append(message))
    dialog_context = SimpleNamespace(
        selected_ids=[source],
        window_manager=SimpleNamespace(invoke_props_dialog=lambda operator, width: {'RUNNING_MODAL'}),
    )
    with bpy.context.temp_override(area=area, selected_ids=[source]):
        assert addon.OUTLINER_OT_batch_remap_user.poll(bpy.context)
    assert addon.OUTLINER_OT_batch_remap_user.invoke(invocation, dialog_context, None) == {'RUNNING_MODAL'}
    assert invocation.target_library == addon.BROWSE_TARGET and not reports

    # Opening (or cancelling) the browser must not link anything. The snapshot
    # stays with its own operator, independent of the browser's selection.
    browser_calls = []
    browser_state = SimpleNamespace(selection_json=json.dumps(records))
    browser_context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=browser_calls.append))
    assert addon.OUTLINER_OT_batch_remap_user_file.invoke(browser_state, browser_context, None) == {'RUNNING_MODAL'}
    assert browser_calls == [browser_state] and not bpy.data.libraries

    try:
        addon.remap_batch(records, str(destination), link_missing=False)
    except ValueError:
        pass
    else:
        raise AssertionError('An unlinked file was accepted with linking disabled')
    assert not bpy.data.libraries and instance.instance_collection == source

    # No match should leave the file unlinked instead of importing unrelated IDs.
    no_match = local_material('NoExactMatch')
    counts = addon.remap_batch(snapshot(no_match), str(destination))
    assert counts[0] == 0 and len(counts[1]) == 1 and not counts[2] and counts[3] == 0, counts
    assert not bpy.data.libraries

    # Execute in the actual FILE_BROWSER area with no Outliner selection context.
    # The file-picker operator creates the first library and remains undoable.
    bpy.context.preferences.edit.use_global_undo = True
    area.type = 'FILE_BROWSER'
    with bpy.context.temp_override(area=area):
        bpy.ops.ed.undo_push(message='Before first link')
        result = bpy.ops.outliner.batch_remap_user_file(
            'EXEC_DEFAULT', filepath=str(destination), selection_json=json.dumps(records),
        )
        assert result == {'FINISHED'}, result
        target = instance.instance_collection
        assert target.library and Path(target.library.filepath).name == destination.name
        assert target.name == 'CollectionTarget'
        assert target in bpy.context.scene.collection.children.values()
        assert source.library is None
        assert untouched.library is None
        assert not any(material.name == 'Metal' and material.library for material in bpy.data.materials)
        bpy.ops.ed.undo_push(message='After first link')
        bpy.ops.ed.undo()
        restored = addon.resolve_source(records[0])
        assert restored and restored.library is None
        assert bpy.data.objects['FirstLinkInstance'].instance_collection == restored
        assert restored in bpy.context.scene.collection.children.values()
        # Blender can retain linked IDs across Undo; scene references must return
        # to the original local collection, and no linked root may remain in use.
        assert all(child.library is None for child in bpy.context.scene.collection.children)
    area.type = 'OUTLINER'
    bpy.ops.wm.read_factory_settings(use_empty=True)


def check_missing_sources(directory):
    source_path = directory / 'OriginalAssets.blend'
    replacement_path = directory / 'ReplacementAssets.blend'
    scene_path = directory / 'SceneWithBrokenLinks.blend'
    source_collection = bpy.data.collections.new('build_004')
    source_collection.objects.link(bpy.data.objects.new('OriginalMember', None))
    source_material = local_material('RecoverMaterial')
    bpy.data.libraries.write(str(source_path), {source_collection, source_material})
    source_collection.name = 'SavedOriginalCollection'
    source_material.name = 'SavedOriginalMaterial'
    replacement_collection = bpy.data.collections.new('build_004')
    replacement_collection.objects.link(bpy.data.objects.new('RecoveredMember', None))
    replacement_material = local_material('RecoverMaterial')
    bpy.data.libraries.write(str(replacement_path), {replacement_collection, replacement_material})
    bpy.ops.wm.read_factory_settings(use_empty=True)

    linked = link(source_path, collections=['build_004'], materials=['RecoverMaterial'])
    bpy.context.scene.collection.children.link(linked.collections[0])
    instance = bpy.data.objects.new('RecoveryInstance', None)
    instance.instance_type = 'COLLECTION'
    instance.instance_collection = linked.collections[0]
    bpy.context.scene.collection.objects.link(instance)
    material_user('RecoveryMaterialUser', linked.materials[0])
    bpy.context.preferences.filepaths.file_preview_type = 'NONE'
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path), relative_remap=False)
    # Only move this test-generated file inside the verified test directory.
    unavailable_path = directory / 'OriginalAssetsUnavailable.blend'
    assert source_path.resolve().parent == directory.resolve()
    assert unavailable_path.resolve().parent == directory.resolve()
    source_path.rename(unavailable_path)
    bpy.ops.wm.open_mainfile(filepath=str(scene_path), load_ui=False)

    instance = bpy.data.objects['RecoveryInstance']
    missing_collection = instance.instance_collection
    missing_material = bpy.data.objects['RecoveryMaterialUser'].data.materials[0]
    assert missing_collection.is_missing and missing_material.is_missing
    assert addon.source_problem(missing_collection) is None
    records = snapshot(missing_collection, missing_material)
    assert all(record['problem'] is None for record in records)
    # A snapshot captured by an older version must not keep blocking recovery.
    for record in records:
        record['problem'] = 'Missing library data-block'
    reports = []
    invocation = SimpleNamespace(selection_json=json.dumps(records), report=lambda severity, message: reports.append(message))
    dialog_context = SimpleNamespace(
        selected_ids=[missing_collection, missing_material],
        window_manager=SimpleNamespace(invoke_props_dialog=lambda operator, width: {'RUNNING_MODAL'}),
    )
    assert addon.OUTLINER_OT_batch_remap_user.invoke(invocation, dialog_context, None) == {'RUNNING_MODAL'}
    assert invocation.target_library == addon.BROWSE_TARGET
    assert not reports and all(record['problem'] is None for record in json.loads(invocation.selection_json))
    assert not any(addon.library_key(lib) == addon.path_key(str(replacement_path)) for lib in bpy.data.libraries)

    area = next(area for area in bpy.context.screen.areas if area.type == 'OUTLINER')
    area.spaces.active.display_mode = 'VIEW_LAYER'
    bpy.context.preferences.edit.use_global_undo = True
    with bpy.context.temp_override(area=area, selected_ids=[missing_collection, missing_material]):
        bpy.ops.ed.undo_push(message='Before missing source recovery')
        # This is the main popup's directly entered file path, not the separate
        # file-browser operator. The replacement file is linked for the first time.
        result = bpy.ops.outliner.batch_remap_user(
            'EXEC_DEFAULT', target_library=addon.BROWSE_TARGET,
            target_path=str(replacement_path), selection_json=json.dumps(records),
        )
        assert result == {'FINISHED'}, result
        recovered = instance.instance_collection
        assert not recovered.is_missing and recovered.name == 'build_004'
        assert recovered.objects.get('RecoveredMember') is not None
        assert addon.library_key(recovered.library) == addon.path_key(str(replacement_path))
        assert recovered in bpy.context.scene.collection.children.values()
        recovered_material = bpy.data.objects['RecoveryMaterialUser'].data.materials[0]
        assert not recovered_material.is_missing
        assert recovered_material.library == recovered.library
        bpy.ops.ed.undo_push(message='After missing source recovery')
        bpy.ops.ed.undo()
        restored_collection = addon.resolve_source(records[0])
        restored_material = addon.resolve_source(records[1])
        assert restored_collection.is_missing and restored_material.is_missing
        assert bpy.data.objects['RecoveryInstance'].instance_collection == restored_collection
        assert bpy.data.objects['RecoveryMaterialUser'].data.materials[0] == restored_material

        # The same missing sources can also use the now-loaded destination library.
        result = bpy.ops.outliner.batch_remap_user(
            'EXEC_DEFAULT', target_library=str(replacement_path), selection_json=json.dumps(records),
        )
        assert result == {'FINISHED'}, result
        assert not bpy.data.objects['RecoveryInstance'].instance_collection.is_missing
    bpy.ops.wm.read_factory_settings(use_empty=True)


def check():
    addon.register()
    workspace = Path(__file__).resolve().parents[1]
    test_root = (workspace / '.test-output').resolve()
    assert test_root.is_relative_to(workspace)
    test_root.mkdir(exist_ok=True)
    with test_directory(test_root) as directory:
        directory = Path(directory)
        assert directory.resolve().is_relative_to(test_root)
        destination = directory / 'AssetLibrary_MiXeD.blend'
        other = directory / 'OtherLibrary_MiXeD.blend'
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

        check_first_link(destination)
        check_missing_sources(directory)

        # Establish two pre-linked libraries; matching data is initially absent.
        loaded = link(destination, materials=['Anchor'])
        anchor = loaded.materials[0]
        other_loaded = link(other, materials=['Elsewhere']).materials[0]
        path = addon.library_path(anchor.library)
        assert Path(path).name == destination.name, path
        target_item = next(item for item in addon.library_items(None, bpy.context) if item[0] == path)
        assert target_item[1].startswith(destination.name + '  '), target_item
        assert destination.name in target_item[2], target_item
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
        # Old saved/scripted paths may have been normalized to lowercase on
        # Windows. Resolve their identity, then load the actual mixed-case path.
        counts = addon.remap_batch(records, os.path.normcase(path))
        assert counts[0] == 3 and len(counts[1]) == 4 and not counts[2] and counts[3] == 3, counts
        target_wood = addon.find_target(wood, 'materials', path)
        assert wood_user.data.materials[0] == target_wood
        assert shared_user.data.materials[0] == target_wood
        assert metal_user.data.materials[0].library == anchor.library
        assert Path(metal_user.data.materials[0].library.filepath).name == destination.name
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
        legacy_record = snapshot(linked_collection)[0]
        legacy_record['library'] = os.path.normcase(legacy_record['library'])
        assert addon.resolve_source(legacy_record) == linked_collection
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
        operator_source = shared_source
        shared_instance.instance_collection = operator_source
        bpy.context.scene.collection.children.link(operator_source)
        operator_records = snapshot(operator_source)
        bpy.context.preferences.edit.use_global_undo = True
        with bpy.context.temp_override(area=area, selected_ids=[operator_source]):
            for display_mode in ('VIEW_LAYER', 'SCENES', 'LIBRARIES', 'ORPHAN_DATA'):
                area.spaces.active.display_mode = display_mode
                assert addon.OUTLINER_OT_batch_remap_user.poll(bpy.context), display_mode
            area.spaces.active.display_mode = 'VIEW_LAYER'
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

        # A missing target must fail before altering users.
        try:
            addon.remap_batch([], str(directory / 'not_linked.blend'))
        except ValueError:
            pass
        else:
            raise AssertionError('Missing target was accepted')
    addon.unregister()
    print('PASS: missing source recovery, popup file path, first link, Outliner modes, exact matches, collections, overrides, Undo')


check()

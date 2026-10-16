"""Run with Blender --background --factory-startup --python-exit-code 1."""
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_integration import link, material_user, test_directory
import batch_remap_user as addon
from batch_remap_user import missing_libraries as finder


def write_assets(filepath, collection_names, marker, include_unmatched=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    material = bpy.data.materials.new('Common')
    material['marker'] = marker
    ids = {material}
    for name in collection_names:
        collection = bpy.data.collections.new(name)
        mesh = bpy.data.meshes.new(name + 'Mesh')
        mesh.materials.append(material)
        obj = bpy.data.objects.new(name + 'Object', mesh)
        obj['marker'] = marker
        collection.objects.link(obj)
        ids.add(collection)
    if include_unmatched:
        ids.add(bpy.data.materials.new('AbsentInChosen'))
    bpy.data.libraries.write(str(filepath), ids)


def check_healthy(healthy_path, image_path):
    material = bpy.data.objects['HealthyUser'].data.materials[0]
    assert addon.library_key(material.library) == addon.path_key(str(healthy_path))
    assert material['marker'] == 'healthy'
    assert bpy.data.images['LocalImage'].filepath == image_path


def check_indirect_library(directory):
    child = directory / 'IndirectChild.blend'
    parent = directory / 'IndirectParent.blend'
    replacement = directory / 'ChosenIndirectReplacement.blend'
    scene = directory / 'IndirectScene.blend'
    write_assets(child, [], 'old-child')
    write_assets(replacement, [], 'new-child')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    child_material = link(child, materials=['Common']).materials[0]
    collection = bpy.data.collections.new('IndirectCollection')
    mesh = bpy.data.meshes.new('IndirectMesh')
    mesh.materials.append(child_material)
    collection.objects.link(bpy.data.objects.new('IndirectObject', mesh))
    bpy.data.libraries.write(str(parent), {collection}, path_remap='RELATIVE_ALL')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = link(parent, collections=['IndirectCollection']).collections[0]
    bpy.context.scene.collection.children.link(collection)
    bpy.context.preferences.filepaths.file_preview_type = 'NONE'
    bpy.ops.wm.save_as_mainfile(filepath=str(scene), relative_remap=True)
    moved = child.with_name('Unavailable_' + child.name)
    assert child.resolve().parent == directory.resolve() == moved.resolve().parent
    child.rename(moved)
    bpy.ops.wm.open_mainfile(filepath=str(scene), load_ui=False)
    assert len(finder.missing_libraries()) == 1
    assert finder.missing_libraries()[0].parent is not None
    processed, remaining, failures, missing_ids = finder.find_missing_linked_libraries(str(replacement))
    # Blender prohibits relocating indirect libraries. Preserve the parent's
    # data, report its file, and never try to rewrite an external blend file.
    assert not processed and len(remaining) == 1 and len(failures) == 1 and len(missing_ids) == 1
    assert str(parent) in failures[0][1], failures
    collection = bpy.context.scene.collection.children.get('IndirectCollection')
    assert addon.library_key(collection.library) == addon.path_key(str(parent))
    assert collection.objects[0].data.materials[0].is_missing
    # Recover the parent dependency on disk within the test fixture, then reload
    # to exercise the operator's clean 'nothing missing' path.
    assert child.resolve().parent == directory.resolve() == moved.resolve().parent
    moved.rename(child)
    bpy.ops.wm.open_mainfile(filepath=str(scene), load_ui=False)
    reports = []
    operator = SimpleNamespace(report=lambda severity, message: reports.append(message))
    assert finder.FILE_OT_find_missing_linked_libraries.invoke(operator, None, None) == {'CANCELLED'}
    assert reports == ['No missing linked libraries']


def check():
    addon.register()
    workspace = Path(__file__).resolve().parents[1]
    test_root = (workspace / '.test-output').resolve()
    assert test_root.is_relative_to(workspace)
    test_root.mkdir(exist_ok=True)
    with test_directory(test_root) as directory:
        original_a = directory / 'OriginalA.blend'
        original_b = directory / 'OriginalB.blend'
        healthy = directory / 'Healthy.blend'
        target = directory / 'ChosenReplacement_MiXeD.blend'
        unrelated = directory / 'Unrelated.blend'
        write_assets(original_a, ['FromA'], 'old-a', include_unmatched=True)
        write_assets(original_b, ['FromB'], 'old-b')
        write_assets(healthy, [], 'healthy')
        write_assets(target, ['FromA', 'FromB'], 'replacement')
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.data.libraries.write(str(unrelated), {bpy.data.materials.new('NoMatchingID')})
        bpy.ops.wm.read_factory_settings(use_empty=True)
        loaded_a = link(original_a, collections=['FromA'], materials=['Common', 'AbsentInChosen'])
        bpy.context.scene.collection.children.link(loaded_a.collections[0])
        material_user('UserA', loaded_a.materials[0])
        material_user('UnmatchedUser', loaded_a.materials[1])
        loaded_b = link(original_b, collections=['FromB'], materials=['Common'])
        bpy.context.scene.collection.children.link(loaded_b.collections[0])
        material_user('UserB', loaded_b.materials[0])
        material_user('HealthyUser', link(healthy, materials=['Common']).materials[0])
        image = bpy.data.images.new('LocalImage', width=1, height=1)
        image.use_fake_user = True
        image.filepath = '//MissingTexture.png'
        image_path = image.filepath
        bpy.context.preferences.filepaths.file_preview_type = 'NONE'
        scene = directory / 'BrokenScene.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(scene), relative_remap=True)
        for original in (original_a, original_b):
            moved = original.with_name('Unavailable_' + original.name)
            assert original.resolve().parent == directory.resolve()
            assert moved.resolve().parent == directory.resolve()
            original.rename(moved)
        bpy.ops.wm.open_mainfile(filepath=str(scene), load_ui=False)
        image_path = bpy.data.images['LocalImage'].filepath
        assert len(finder.missing_libraries()) == 2
        assert bpy.data.objects['UserA'].data.materials[0].is_missing
        assert bpy.data.objects['UserB'].data.materials[0].is_missing
        check_healthy(healthy, image_path)

        # No matching data: preserve every broken reference, create no target link.
        processed, remaining, failures, missing_ids = finder.find_missing_linked_libraries(str(unrelated))
        assert not processed and len(remaining) == 2 and not failures and missing_ids
        assert not any(addon.library_key(lib) == addon.path_key(str(unrelated)) for lib in bpy.data.libraries)

        # Invoke opens a blend file picker, without needing any data selection.
        calls = []
        reports = []
        operator = SimpleNamespace(report=lambda severity, message: reports.append(message))
        context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=calls.append))
        assert finder.FILE_OT_find_missing_linked_libraries.invoke(operator, context, None) == {'RUNNING_MODAL'}
        assert calls == [operator] and not reports
        assert len(finder.missing_libraries()) == 2

        for obj in bpy.context.scene.objects:
            if obj.library is None:
                obj.select_set(False)
        bpy.context.view_layer.objects.active = None
        area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        bpy.context.preferences.edit.use_global_undo = True
        with bpy.context.temp_override(area=area):
            assert finder.FILE_OT_find_missing_linked_libraries.poll(bpy.context)
            assert not bpy.context.selected_objects
            bpy.ops.ed.undo_push(message='Before library recovery')
            result = bpy.ops.file.find_missing_linked_libraries('EXEC_DEFAULT', filepath=str(target))
            assert result == {'FINISHED'}, result
            material_a = bpy.data.objects['UserA'].data.materials[0]
            material_b = bpy.data.objects['UserB'].data.materials[0]
            assert material_a == material_b and not material_a.is_missing
            assert material_a['marker'] == 'replacement'
            assert addon.library_key(material_a.library) == addon.path_key(str(target))
            assert material_a.library.filepath.startswith('//')
            assert Path(material_a.library.filepath[2:]).name == target.name, material_a.library.filepath
            for name in ('FromA', 'FromB'):
                collection = bpy.context.scene.collection.children.get(name)
                assert collection and not collection.is_missing
                assert collection.objects[0]['marker'] == 'replacement'
            unmatched = bpy.data.objects['UnmatchedUser'].data.materials[0]
            assert unmatched.is_missing
            assert addon.library_key(unmatched.library) == addon.path_key(str(original_a))
            assert len(finder.missing_libraries()) == 1
            check_healthy(healthy, image_path)
            bpy.ops.ed.undo_push(message='After library recovery')
            bpy.ops.ed.undo()
            assert bpy.data.objects['UserA'].data.materials[0].is_missing
            assert bpy.data.objects['UserB'].data.materials[0].is_missing
            assert len(finder.missing_libraries()) == 2
            check_healthy(healthy, image_path)
        check_indirect_library(directory)
    addon.unregister()
    print('PASS: explicit blend, no selection, multiple libraries, partial matches, indirect reporting, healthy libraries, relative paths, Undo')


if __name__ == '__main__':
    check()

"""Build the Install from Disk extension archive with the standard library."""
from pathlib import Path
import ast
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
source = root
manifest = tomllib.loads((source / 'blender_manifest.toml').read_text(encoding='utf-8'))
ast.parse((source / '__init__.py').read_text(encoding='utf-8'))
ast.parse((source / 'batch_remap_user' / '__init__.py').read_text(encoding='utf-8'))
assert len(manifest['tagline']) <= 64
destination = root / 'dist' / f"{manifest['id']}-{manifest['version']}.zip"
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in ('__init__.py', 'blender_manifest.toml', 'batch_remap_user/__init__.py',
                 'batch_remap_user/missing_libraries.py'):
        archive.write(source / name, name)
    archive.write(root / 'README.md', 'README.md')
print(destination)

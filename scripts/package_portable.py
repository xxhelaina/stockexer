"""Archive only generated runtime files, excluding local smoke-test/cache data."""
import sys
import zipfile
from pathlib import Path


def package(folder, destination):
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    assert (folder / 'StockLab.exe').is_file() and (folder / '_internal').is_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob('*')):
            relative = path.relative_to(folder)
            if relative.parts[0] == 'data' or '__pycache__' in relative.parts or path.suffix == '.log':
                continue
            if path.is_file():
                archive.write(path, str(Path('StockLab') / relative))
    print(f'{destination.name}: {destination.stat().st_size / 1024**2:.1f} MiB')


if __name__ == '__main__':
    package(*sys.argv[1:])

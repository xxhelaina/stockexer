"""Copy verbatim dependency notices and write the release component manifest."""
import json
import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path


def main(destination):
    destination = Path(destination).resolve()
    notices = destination / 'licenses'
    notices.mkdir(parents=True, exist_ok=True)
    components = []
    for dist in sorted(distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = dist.metadata['Name'], dist.version
        folder = notices / name
        folder.mkdir(exist_ok=True)
        copied = []
        for entry in dist.files or []:
            if any(word in Path(str(entry)).name.lower() for word in ('license', 'copying', 'notice')):
                path = Path(dist.locate_file(entry))
                if path.is_file():
                    relative = Path(str(entry))
                    # Preserve nested package attribution files, without parent traversals.
                    safe = Path(*[p for p in relative.parts if p not in ('..', '.')])
                    target = folder / safe
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    copied.append(str(target.relative_to(destination)))
        license_text = dist.metadata.get('License', '')
        if license_text:
            (folder / 'METADATA-LICENSE.txt').write_text(license_text, encoding='utf-8')
        (folder / 'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        components.append({'name': name, 'version': version,
            'license_expression': dist.metadata.get('License-Expression'),
            'source': f'https://pypi.org/project/{name}/{version}/#files', 'notices': copied})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        (notices / 'CPython').mkdir(exist_ok=True)
        shutil.copy2(python_license, notices / 'CPython' / 'LICENSE.txt')
    (destination / 'components.json').write_text(json.dumps(components, indent=2), encoding='utf-8')
    text = '''# Third-party components / 第三方组件

StockLab source is GPLv3. Dependencies retain their own licenses.
This bundle contains CPython, PyQt6, Qt, NumPy, pandas, Matplotlib, requests,
BaoStock and their dependencies. Exact versions and source links: components.json.
Verbatim available notices and distribution metadata: licenses/.
Build-only dependency notices may also be included for completeness.

PyQt6 is GPL-3.0-only. Its exact source archive is supplied in the same Release's
source attachment, together with the StockLab application source and build scripts.
Qt 6.9.2 is supplied as replaceable shared libraries under LGPLv3.
Qt source and component attributions:
https://download.qt.io/archive/qt/6.9/6.9.2/single/
https://doc.qt.io/qt-6.9/licenses-used-in-qt.html
The application does not prohibit replacement with compatible Qt libraries or
reverse engineering for debugging changes to the LGPL library.

CPython source: https://www.python.org/downloads/source/
Other dependency sources: exact-version PyPI file links in components.json.
No project license replaces any dependency's license or author notices.
'''
    (destination / 'THIRD_PARTY_NOTICES.md').write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1])

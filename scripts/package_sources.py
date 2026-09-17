"""Bundle the exact application revision and frozen GPL component sources."""
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def main(version):
    root = Path(__file__).resolve().parent.parent
    output = root / 'release' / f'StockLab-v{version}-sources.zip'
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='stocklab-sources-') as folder:
        folder = Path(folder)
        project = folder / f'StockLab-v{version}-source.zip'
        subprocess.run(['git', 'archive', '--format=zip', '--prefix=StockLab/',
                        f'--output={project}', revision], cwd=root, check=True)
        sources = []
        for package, package_version in [('PyQt6', '6.9.1'), ('PyQt6_sip', '13.12.0'),
                                          ('pyinstaller', '6.22.3'), ('baostock', '0.9.3')]:
            with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{package_version}/json', timeout=30) as response:
                metadata = json.load(response)
            source = next(entry for entry in metadata['urls'] if entry['packagetype'] == 'sdist')
            with urllib.request.urlopen(source['url'], timeout=60) as response:
                content = response.read()
            assert hashlib.sha256(content).hexdigest() == source['digests']['sha256'], package
            target = folder / source['filename']
            target.write_bytes(content)
            sources.append({'package': package, 'version': package_version,
                'filename': source['filename'], 'url': source['url'], 'sha256': source['digests']['sha256']})
        manifest = folder / 'SOURCE-MANIFEST.json'
        manifest.write_text(json.dumps({'revision': revision, 'bundled_sources': sources,
            'qt_source': 'https://download.qt.io/archive/qt/6.9/6.9.2/single/'}, indent=2), encoding='utf-8')
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()):
                archive.write(path, path.name)
    print(f'{output.name}: {output.stat().st_size / 1024**2:.1f} MiB; revision {revision}')


if __name__ == '__main__':
    main(sys.argv[1])

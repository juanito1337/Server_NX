"""Complete collector manifests stay within the import contract, no actual NX."""
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nxserver import nxworker, resultworker
from nxserver.libraries import SPECS
from nxserver.results import Results, MAX_MANIFEST, parse_package
from nxserver.safety import DomainError, offline_path
from nxserver.store import canonical
from test_results import result_pilot, table
from test_libraries import write


@pytest.fixture
def collector(result_pilot, tmp_path, monkeypatch):
    _, store, _, resource, job = result_pilot
    raw, _ = Results(store).collector(job['id'], 'tool-metric', 'CUSTOM', 'fixture')
    directories = []
    monkeypatch.setattr(resultworker, 'job_directory', offline_path)
    monkeypatch.setattr(resultworker, 'check_environment', lambda session: None)

    def create(count=1, name='Synthetic body'):
        directory = offline_path(tmp_path / ('collector_' + str(len(directories))))
        directory.mkdir()
        directories.append(directory)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            archive.extractall(directory)
        local = offline_path(directory / 'return-library')
        graphics = offline_path(local / SPECS['tool-metric']['graphics_dir'])
        graphics.mkdir(parents=True)
        model = offline_path(graphics / 'CUSTOM.prt')
        model.write_bytes(b'Synthetic PRT, not actual CAD')
        write(local, SPECS['tool-metric']['path'], table(['LIBRF', 'DESCR', 'HLDREF'], [('CUSTOM', 'Synthetic', '')]))
        part = SimpleNamespace(FullPath=str(model), IsModified=False, PartUnits='Millimeters', IsFullyLoaded=True,
            ComponentAssembly=SimpleNamespace(RootComponent=None),
            Bodies=[SimpleNamespace(JournalIdentifier='BODY ' + str(index), Name=name,
                IsSolidBody=True, IsConvergentBody=False, GetFaces=lambda: [], Layer=1, Color=42)
                for index in range(count)])

        class Parts(list):
            Work = part

        session = SimpleNamespace(Parts=Parts([part]))
        original = {path.relative_to(directory).as_posix(): path.read_bytes()
                    for path in directory.rglob('*') if path.is_file()}
        return directory, session, original

    return create, store, resource, job


def assert_originals(fixture, directory, session, original):
    _, store, resource, _ = fixture
    for relative, data in original.items():
        assert (directory / relative).read_bytes() == data
    assert not session.Parts.Work.IsModified
    assert store.resources()[0] == resource and resource['cam_status'] == 'draft'
    assert store.active()['release_id'] is None


def read_manifest(path):
    with zipfile.ZipFile(path) as archive:
        return archive.read('manifest.json')


def test_collector_manifest_limit_matches_server():
    assert resultworker.MAX_MANIFEST_BYTES == MAX_MANIFEST == 2 * 1024 * 1024


@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_exact_utf8_manifest_size_boundary_preserves_all_inventory(collector, monkeypatch, delta):
    create, _, _, job = collector
    first, session, _ = create(name='Fräser – Prüfung Ø12')
    baseline = resultworker.collect(first, session)
    expected = read_manifest(baseline['file'])
    monkeypatch.setattr(resultworker, 'MAX_MANIFEST_BYTES', len(expected) + delta)
    directory, session, original = create(name='Fräser – Prüfung Ø12')
    output = directory / 'prepared-result.zip'
    if delta < 0:
        with pytest.raises(DomainError, match='Prepared manifest exceeds'):
            resultworker.collect(directory, session)
        assert not output.exists()
    else:
        result = resultworker.collect(directory, session)
        actual = read_manifest(output)
        assert actual == expected
        parsed = parse_package(output.read_bytes(), job)
        assert actual == canonical(parsed).encode('utf-8')
        assert parsed['parts'][0]['bodies'][0]['name'] == 'Fräser – Prüfung Ø12'
        assert not result['automatic_cam_approval']
    assert_originals(collector, directory, session, original)


def test_server_size_limit_is_checked_before_constructing_an_archive(collector, monkeypatch):
    # Real server limit and field-valid metadata, not a reduced fake limit.
    create, *_ = collector
    directory, session, original = create(count=1100, name='ä' * 1000)
    zip_calls = []

    def forbidden(*args, **kwargs):
        zip_calls.append(args)
        pytest.fail('Oversized metadata must stop before constructing the result ZIP.')

    monkeypatch.setattr(resultworker.zipfile, 'ZipFile', forbidden)
    with pytest.raises(DomainError, match='Prepared manifest exceeds 2 MiB'):
        resultworker.collect(directory, session)
    assert not zip_calls and not (directory / 'prepared-result.zip').exists()
    assert len(session.Parts.Work.Bodies) == 1100
    assert all(item.Name == 'ä' * 1000 for item in session.Parts.Work.Bodies)
    assert_originals(collector, directory, session, original)


@pytest.mark.parametrize('count', [2, 3])
def test_shared_body_bound_does_not_publish_an_incomplete_collector_inventory(collector, monkeypatch, count):
    create, _, _, job = collector
    monkeypatch.setattr(nxworker, 'MAX_PART_BODIES', 2)
    directory, session, original = create(count=count)
    output = directory / 'prepared-result.zip'
    if count > 2:
        with pytest.raises(DomainError, match='Body count exceeds'):
            resultworker.collect(directory, session)
        assert not output.exists()
    else:
        resultworker.collect(directory, session)
        parsed = parse_package(output.read_bytes(), job)
        assert len(parsed['parts'][0]['bodies']) == count
        assert not parsed['automatic_cam_approval']
    assert_originals(collector, directory, session, original)


def test_existing_result_archive_is_not_replaced_even_with_an_invalid_new_inventory(collector, monkeypatch):
    create, *_ = collector
    directory, session, original = create()
    resultworker.collect(directory, session)
    output = directory / 'prepared-result.zip'
    saved = output.read_bytes()
    monkeypatch.setattr(resultworker, 'MAX_MANIFEST_BYTES', 1)
    with pytest.raises(DomainError, match='already exists'):
        resultworker.collect(directory, session)
    assert output.read_bytes() == saved
    assert_originals(collector, directory, session, original)

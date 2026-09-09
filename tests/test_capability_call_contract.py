import json
from src.capability_adapters.models import CapabilityRuntimeManifest, RuntimeCommand

import pytest

from tests.test_capability_reuse import pack


def runtime_manifest(kind):
    return CapabilityRuntimeManifest(schema_version=1, name='native-echo', version='1.0.0',
        kind=kind, purpose='固定样本检查', entrypoint=RuntimeCommand(program='node'), permissions=('network:none',))


def declared_pack(**changes):
    result = pack()
    metadata = dict(result.manifest)
    contract = json.loads(metadata['reuse_contract'])
    contract['parameters_schema'] = {'type': 'array', 'items': {'type': 'string', 'enum': ['sample']}, 'minItems': 1, 'maxItems': 1}
    contract.update(changes)
    metadata['reuse_contract'] = json.dumps(contract)
    result.manifest = tuple(metadata.items())
    return result


def test_actual_call_requires_readonly_contract_and_valid_arguments():
    from src.capability_catalog.reuse import validate_reuse_call

    runtime = runtime_manifest('node')
    validate_reuse_call(declared_pack(), runtime, ['sample'], None)
    for candidate, arguments in [(declared_pack(), ['unsafe']), (declared_pack(side_effect='write'), ['sample']), (declared_pack(parameters_schema={}), ['sample'])]:
        with pytest.raises(ValueError):
            validate_reuse_call(candidate, runtime, arguments, None)


def test_mcp_tools_and_remote_schema_references_fail_closed():
    from src.capability_catalog.reuse import validate_reuse_call

    runtime = runtime_manifest('mcp_local')
    candidate = declared_pack(parameters_schema={'type': 'object', 'additionalProperties': False})
    with pytest.raises(ValueError):
        validate_reuse_call(candidate, runtime, {}, 'delete')
    candidate.manifest += (('reuse_tools', '["read"]'),)
    validate_reuse_call(candidate, runtime, {}, 'read')
    with pytest.raises(ValueError):
        validate_reuse_call(candidate, runtime, {}, 'delete')
    with pytest.raises(ValueError):
        validate_reuse_call(declared_pack(parameters_schema={'type': 'array', '$ref': 'https://outside.test/schema'}), runtime_manifest('node'), ['sample'], None)

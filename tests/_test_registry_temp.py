"""Temporary integration test for MachineRegistry."""
import asyncio
import sys
sys.path.insert(0, '.')

from db.async_database import AsyncDatabase
from engine.machine_registry import MachineRegistry

SCHEMA = """CREATE TABLE machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    rtsp_url_encrypted TEXT NOT NULL,
    detection_zone TEXT NOT NULL DEFAULT '(0.0, 0.0, 1.0, 1.0)',
    ocr_zone TEXT NOT NULL DEFAULT '{"x1": 0.30, "y1": 0.10, "x2": 0.70, "y2": 0.55}',
    person_confidence_threshold REAL NOT NULL DEFAULT 0.60,
    light_zone TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""


async def test_registry():
    db = AsyncDatabase(db_path=':memory:')
    await db.connect()
    await db.execute(SCHEMA)

    registry = MachineRegistry(db)

    # Test register
    m1 = await registry.register_machine('M-01', 'Lathe 1', 'rtsp://cam:pass@10.0.0.1:554/s')
    assert m1['machine_id'] == 'M-01'
    assert m1['display_name'] == 'Lathe 1'
    assert m1['rtsp_url'] == 'rtsp://cam:pass@10.0.0.1:554/s'
    assert m1['status'] == 'active'
    print('register_machine OK')

    # Test duplicate rejection
    try:
        await registry.register_machine('M-01', 'Dup', 'rtsp://a@b:554/x')
        assert False, 'Should have raised'
    except ValueError as e:
        assert 'already registered' in str(e)
    print('Duplicate rejection OK')

    # Register more machines
    await registry.register_machine('M-02', 'Drill 1', 'rtsp://10.0.0.2:554/s', status='inactive')
    await registry.register_machine('M-03', 'CNC 1', '/dev/video0')

    # Test get_all_machines
    all_machines = await registry.get_all_machines()
    assert len(all_machines) == 3
    print(f'get_all_machines OK: {len(all_machines)} machines')

    # Test get_active_machines
    active = await registry.get_active_machines()
    assert len(active) == 2  # M-01 and M-03
    print(f'get_active_machines OK: {len(active)} active')

    # Test update_machine
    updated = await registry.update_machine('M-01', display_name='Lathe 1 Updated', person_confidence_threshold=0.75)
    assert updated['display_name'] == 'Lathe 1 Updated'
    assert updated['person_confidence_threshold'] == 0.75
    print('update_machine OK')

    # Test deactivate/activate
    deactivated = await registry.deactivate_machine('M-01')
    assert deactivated['status'] == 'inactive'
    activated = await registry.activate_machine('M-01')
    assert activated['status'] == 'active'
    print('activate/deactivate OK')

    # Test delete
    deleted = await registry.delete_machine('M-03')
    assert deleted is True
    assert await registry.get_machine('M-03') is None
    not_deleted = await registry.delete_machine('M-99')
    assert not_deleted is False
    print('delete_machine OK')

    # Test 8+ concurrent machines
    for i in range(4, 12):
        await registry.register_machine(f'M-{i:02d}', f'Machine {i}', f'rtsp://10.0.0.{i}:554/s')
    all_m = await registry.get_all_machines()
    assert len(all_m) >= 8, f'Expected >= 8 machines, got {len(all_m)}'
    print(f'Supports {len(all_m)} concurrent machines OK (>= 8)')

    # Test update non-existing machine
    result = await registry.update_machine('M-99', display_name='Nope')
    assert result is None
    print('update_machine returns None for missing OK')

    # Test get_machine returns None for missing
    result = await registry.get_machine('M-MISSING')
    assert result is None
    print('get_machine returns None for missing OK')

    await db.close()
    print()
    print('All registry integration tests passed!')


if __name__ == '__main__':
    asyncio.run(test_registry())

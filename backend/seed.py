"""
Seed script — creates a Garage, one Floor with 500 available spots,
entry + exit gates, and an admin staff account.
"""
from app import app, db
from models import (
    Garage, Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum,
    GateEvent, GateTypeEnum, GateStatusEnum,
)

with app.app_context():
    # Hard reset — wipe existing structure data before seeding
    ParkingSpot.query.delete()
    GateEvent.query.delete()
    Floor.query.delete()
    Garage.query.delete()
    db.session.commit()
    print("Reset: cleared existing Garage, Floor, ParkingSpot, and GateEvent data.")

    # Garage
    garage = Garage(
        name="GarageFlow Main",
        total_capacity=500,
        number_of_floors=1,
        operating_hours="6:00am-midnight",
        front_desk_phone="555-0100",
    )
    db.session.add(garage)
    db.session.flush()

    # Floor
    floor = Floor(
        garage_id=garage.garage_id,
        floor_number=1,
        floor_name="Ground",
        total_spots=500,
        available_spots=500,
    )
    db.session.add(floor)
    db.session.flush()

    # 500 standard ParkingSpots
    for i in range(1, 501):
        db.session.add(ParkingSpot(
            floor_id=floor.floor_id,
            spot_type=SpotTypeEnum.standard,
            status=SpotStatusEnum.available,
            location_reference=f"A-{i:03d}",
        ))

    # Entry + exit gates (required by tickets and reservations)
    entry_gate = GateEvent(
        garage_id=garage.garage_id,
        gate_type=GateTypeEnum.entry,
        status=GateStatusEnum.open,
    )
    exit_gate = GateEvent(
        garage_id=garage.garage_id,
        gate_type=GateTypeEnum.exit,
        status=GateStatusEnum.open,
    )
    db.session.add(entry_gate)
    db.session.add(exit_gate)

    db.session.commit()
    print(f"Seeded: {garage}")
    print(f"Seeded: {floor}, 500 standard spots")
    print(f"Seeded: entry gate (id={entry_gate.gate_id}), exit gate (id={exit_gate.gate_id})")

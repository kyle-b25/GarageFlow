"""
Seed script — creates a Garage, one Floor with 10 available spots,
and 5 standard ParkingSpots.
"""
from app import app, db
from models import Garage, Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum

with app.app_context():
    # Hard reset — wipe existing structure data before seeding
    ParkingSpot.query.delete()
    Floor.query.delete()
    Garage.query.delete()
    db.session.commit()
    print("Reset: cleared existing Garage, Floor, and ParkingSpot data.")
    # Garage
    garage = Garage(
        name="GarageFlow Main",
        total_capacity=5,
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
        total_spots=5,
        available_spots=5,
    )
    db.session.add(floor)
    db.session.flush()

    # 5 standard ParkingSpots
    for i in range(1, 6):
        db.session.add(ParkingSpot(
            floor_id=floor.floor_id,
            spot_type=SpotTypeEnum.standard,
            status=SpotStatusEnum.available,
            location_reference=f"A-{i:02d}",
        ))

    db.session.commit()
    print(f"Seeded: {garage}, {floor}, 5 standard spots")

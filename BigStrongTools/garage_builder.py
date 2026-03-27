"""
garage_builder.py — GarageFlow Interactive Garage Builder

Run this from your garageflow project root:
    python garage_builder.py

It will prompt you to build your garage floor by floor, zone by zone,
then write everything to the database using your existing models.

Wipes any existing Garage, Floor, and ParkingSpot data before seeding.
"""

import sys
import os

#Path such that it can be run from inside "tests"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from app import app, db
    from models import (
        Garage, Floor, ParkingSpot,
        SpotTypeEnum, SpotStatusEnum,
    )
except ImportError as e:
    print(f"\n❌  Could not import GarageFlow modules: {e}")
    print("    Make sure you run this script from your garageflow project root.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def ask(prompt, default=None):
    """Prompt the user for a string. Re-prompts if blank and no default."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"  {prompt}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return str(default)
        print("    ⚠  This field is required.")


def ask_int(prompt, default=None, min_val=1, max_val=9999):
    """Prompt the user for an integer within [min_val, max_val]."""
    while True:
        raw = ask(prompt, default)
        try:
            val = int(raw)
        except ValueError:
            print(f"    ⚠  Please enter a whole number.")
            continue
        if val < min_val or val > max_val:
            print(f"    ⚠  Must be between {min_val} and {max_val}.")
            continue
        return val


def ask_yes_no(prompt, default=True):
    """Prompt the user for y/n."""
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("    ⚠  Please enter y or n.")


def divider(char="─", width=56):
    print(char * width)


def header(title):
    divider("═")
    print(f"  {title}")
    divider("═")


# Spot types the builder supports, mapped to SpotTypeEnum values
SPOT_TYPES = {
    "standard":      SpotTypeEnum.standard,
    "accessibility": SpotTypeEnum.accessibility,
    "staff":         SpotTypeEnum.staff,   # covers employee + eco conceptually
}

SPOT_TYPE_LABELS = {
    "standard":      "Standard",
    "accessibility": "Accessibility",
    "staff":         "Staff / Employee / Eco",
}

ZONE_LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


# ─────────────────────────────────────────────────────────────────────────────
#  Floor builder
# ─────────────────────────────────────────────────────────────────────────────

def build_floor(floor_index, garage_id):
    """
    Interactively collect all data for one floor.
    Returns a dict ready to be committed as Floor + ParkingSpot rows.
    """
    header(f"FLOOR {floor_index}")

    floor_number = ask_int("Floor number (e.g. -1 for basement, 1, 2 …)", default=floor_index)
    floor_name   = ask("Floor name (e.g. Ground, Rooftop, Basement — or leave blank)", default="")
    floor_name   = floor_name if floor_name else None

    print()
    print("  How many zones does this floor have?")
    print("  Each zone gets a letter (A, B, C …) and its own spot counts.")
    num_zones = ask_int("Number of zones", default=1, min_val=1, max_val=26)

    zones = []
    floor_total = 0

    for z in range(num_zones):
        zone_letter = ZONE_LETTERS[z]
        divider()
        print(f"  Zone {zone_letter}")
        divider()

        zone_spots = []

        for type_key, type_enum in SPOT_TYPES.items():
            label = SPOT_TYPE_LABELS[type_key]
            count = ask_int(f"  {label} spots in Zone {zone_letter}", default=0, min_val=0)

            for i in range(1, count + 1):
                location_ref = f"{zone_letter}-{i:02d}-{type_key[:3].upper()}"
                zone_spots.append({
                    "spot_type":          type_enum,
                    "status":             SpotStatusEnum.available,
                    "location_reference": location_ref,
                })
                floor_total += 1

        zones.append(zone_spots)

    all_spots = [spot for zone in zones for spot in zone]

    print()
    print(f"  ✔  Floor {floor_number} summary: {floor_total} total spots across {num_zones} zone(s).")
    return {
        "floor_number":    floor_number,
        "floor_name":      floor_name,
        "total_spots":     floor_total,
        "available_spots": floor_total,
        "spots":           all_spots,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Main builder
# ─────────────────────────────────────────────────────────────────────────────

def main():
    header("GARAGEFLOW INTERACTIVE GARAGE BUILDER")
    print()
    print("  This script will guide you through creating a garage,")
    print("  its floors, zones, and parking spots.")
    print()
    print("  ⚠  Existing Garage, Floor, and ParkingSpot data will be")
    print("     cleared before the new data is written.")
    print()

    if not ask_yes_no("Ready to start?", default=True):
        print("\n  Cancelled. No changes made.\n")
        sys.exit(0)

    # ── Garage details ────────────────────────────────────────────────────────
    print()
    header("GARAGE DETAILS")
    garage_name    = ask("Garage name", default="GarageFlow Main")
    operating_hrs  = ask("Operating hours (e.g. 6:00am–midnight)", default="6:00am–midnight")
    front_desk_ph  = ask("Front-desk phone", default="555-0100")

    # ── Floor loop ────────────────────────────────────────────────────────────
    print()
    num_floors = ask_int("How many floors does this garage have?", default=1, min_val=1, max_val=20)

    floor_data = []
    for i in range(1, num_floors + 1):
        print()
        floor_data.append(build_floor(i, garage_id=None))  # garage_id assigned after insert

    # ── Summary ───────────────────────────────────────────────────────────────
    grand_total = sum(f["total_spots"] for f in floor_data)
    print()
    header("SUMMARY")
    print(f"  Garage  : {garage_name}")
    print(f"  Floors  : {num_floors}")
    print(f"  Spots   : {grand_total} total")
    print()
    for fd in floor_data:
        name_str = f" ({fd['floor_name']})" if fd["floor_name"] else ""
        print(f"    Floor {fd['floor_number']}{name_str}  —  {fd['total_spots']} spots")
    print()

    if not ask_yes_no("Commit this to the database?", default=True):
        print("\n  Cancelled. No changes made.\n")
        sys.exit(0)

    # ── Write to DB ───────────────────────────────────────────────────────────
    print()
    print("  Writing to database …")

    with app.app_context():
        # Clear existing structure data
        ParkingSpot.query.delete()
        Floor.query.delete()
        Garage.query.delete()
        db.session.commit()

        # Create Garage
        garage = Garage(
            name=garage_name,
            total_capacity=grand_total,
            number_of_floors=num_floors,
            operating_hours=operating_hrs,
            front_desk_phone=front_desk_ph,
        )
        db.session.add(garage)
        db.session.flush()

        # Create Floors + Spots
        for fd in floor_data:
            floor = Floor(
                garage_id=garage.garage_id,
                floor_number=fd["floor_number"],
                floor_name=fd["floor_name"],
                total_spots=fd["total_spots"],
                available_spots=fd["available_spots"],
            )
            db.session.add(floor)
            db.session.flush()

            for spot_def in fd["spots"]:
                db.session.add(ParkingSpot(
                    floor_id=floor.floor_id,
                    spot_type=spot_def["spot_type"],
                    status=spot_def["status"],
                    location_reference=spot_def["location_reference"],
                ))

        db.session.commit()

    print()
    divider("═")
    print(f"  ✅  Done! Garage '{garage_name}' created with {grand_total} spots")
    print(f"      across {num_floors} floor(s).")
    divider("═")
    print()
    print("  You can now start your Flask server and use the kiosk.")
    print()


if __name__ == "__main__":
    main()

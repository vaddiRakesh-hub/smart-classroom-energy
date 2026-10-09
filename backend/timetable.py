"""Classroom master data and a deterministic demo timetable."""
import random

ROOMS = [
    dict(id=1, name="CS-101",  kind="Lecture hall", building="Block A", capacity=60,  light_w=240, fan_w=300, ac_w=1500),
    dict(id=2, name="CS-102",  kind="Lecture hall", building="Block A", capacity=60,  light_w=240, fan_w=300, ac_w=0),
    dict(id=3, name="EC-201",  kind="Classroom",    building="Block B", capacity=48,  light_w=160, fan_w=300, ac_w=0),
    dict(id=4, name="ME-202",  kind="Classroom",    building="Block B", capacity=48,  light_w=160, fan_w=300, ac_w=0),
    dict(id=5, name="LAB-1",   kind="Computer lab", building="Block C", capacity=40,  light_w=320, fan_w=225, ac_w=2200),
    dict(id=6, name="SEM-1",   kind="Seminar hall", building="Block C", capacity=120, light_w=480, fan_w=450, ac_w=3000),
]

SUBJECTS = ["Data Structures", "Operating Systems", "Digital Electronics", "Thermodynamics",
            "Machine Learning", "Engineering Maths", "Networks", "Signals and Systems",
            "Database Systems", "Control Systems", "Software Engineering", "Python Lab"]

# (start_minute, end_minute) of the hourly teaching blocks
BLOCKS = [(540, 600), (600, 660), (675, 735), (780, 840), (840, 900), (900, 960)]


def build_timetable(seed=42):
    """Return a list of timetable rows for Mon-Fri. Deterministic for a given seed."""
    rng = random.Random(seed)
    rows = []
    for room in ROOMS:
        for dow in range(5):
            for start, end in rng.sample(BLOCKS, rng.randint(3, 5)):
                students = round(room["capacity"] * rng.uniform(0.6, 0.95))
                rows.append(dict(classroom_id=room["id"], dow=dow, start_min=start, end_min=end,
                                 subject=rng.choice(SUBJECTS), students=students))
    return rows


def slots_for(timetable, room_id, dow, capacity):
    """[(start, end, expected_ratio)] for one room/day, sorted by start."""
    out = [(r["start_min"], r["end_min"], r["students"] / capacity)
           for r in timetable if r["classroom_id"] == room_id and r["dow"] == dow]
    return sorted(out)

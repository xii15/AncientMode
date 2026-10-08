"""elegant_melody_01.py

Generate a quiet, florid melody made of short snippets separated by rests.
Requires matrix.py in the same folder. Uses only Python's standard library.
"""
from pathlib import Path
import math
import random
import struct

from matrix import apply_motion, motion_value

# -----------------------------------------------------------------------------
# COMPOSITION SETTINGS
# -----------------------------------------------------------------------------
OUTPUT_FILE = Path(__file__).resolve().parent / "elegant_melodyHRP_v2.mid"
LOW_NOTE = 66 #harp 30-90 #tongue drum 54-78 kbdA 50-90 #66-80 tpt srednja laga
HIGH_NOTE = 80
START_NOTE = 75

TEMPO_BPM = 60
TIME_SIGNATURE = (4, 4)
MEASURES = 6                 # Chosen default; change freely.
VELOCITY_MIN = 100
VELOCITY_MAX = 122
PROGRAM = 73                  # General MIDI Flute, zero-based.
CHANNEL = 0
TICKS_PER_BEAT = 960          # Divisible by 32nd-note duration.
RANDOM_SEED = 8888            # None creates a fresh result each run.

# Quarter-note beat values.
THIRTY_SECOND = 0.125
MAIN_DURATIONS = (0.75, 1.0, 1.5, 2.0, 3.0)  # dotted 8th, 4th, dotted 4th, half, dotted half
MAIN_DURATION_WEIGHTS = (16, 28, 24, 20, 12)
REST_DURATIONS = (1.5, 1.0)                    # eighth or quarter rest
REST_WEIGHTS = (3, 2)

# A snippet contains one main note most often, two sometimes, three rarely.
SNIPPET_NOTE_COUNTS = (1, 2, 3)
SNIPPET_NOTE_COUNT_WEIGHTS = (50, 30, 20)

# Main-note motion families. Stepwise motion is favoured, but the line remains florid.
MAIN_MOTIONS = ("R", "L", "U", "D","U2", "D2") # "U2", "D2", "U3", "D3", "U4", "D4",
MAIN_MOTION_WEIGHTS = (25, 25, 25, 25, 10, 10)

# Prefix ornaments prefer semitone motion; U/D are rare. Compound ornamental leaps are excluded.
ORNAMENT_MOTIONS = ("L", "R", "U", "D")
ORNAMENT_MOTION_WEIGHTS = (25, 25, 25, 25)
PREFIX_ORNAMENT_COUNTS = (0, 1, 2, 3, 4)
PREFIX_ORNAMENT_COUNT_WEIGHTS = (24, 27, 23, 16, 10)

# End ornaments occur only at the end of a snippet, with a maximum of two.
ENDING_ORNAMENT_COUNTS = (0, 1, 2)
ENDING_ORNAMENT_COUNT_WEIGHTS = (58, 29, 13)


def weighted_valid_motion(note, motions, weights, rng):
    """Choose a weighted motion whose destination remains inside the range."""
    valid=[]
    valid_weights=[]
    for motion, weight in zip(motions, weights):
        destination=note+motion_value(motion)
        if LOW_NOTE <= destination <= HIGH_NOTE:
            valid.append(motion)
            valid_weights.append(weight)
    if not valid:
        raise RuntimeError(f"No valid motion from MIDI note {note}.")
    return rng.choices(valid, weights=valid_weights, k=1)[0]


def choose_duration_that_fits(remaining_beats, rng):
    choices=[]
    weights=[]
    for duration, weight in zip(MAIN_DURATIONS, MAIN_DURATION_WEIGHTS):
        if duration <= remaining_beats:
            choices.append(duration)
            weights.append(weight)
    if not choices:
        return None
    return rng.choices(choices, weights=weights, k=1)[0]


def append_note(events, pitch, duration, rng, role):
    if duration <= 0:
        raise ValueError("A note duration became non-positive.")
    events.append(("note", duration, pitch, rng.randint(VELOCITY_MIN, VELOCITY_MAX), role))


def generate_prefix_ornaments(events, current_note, count, rng):
    """Create 0-4 32nd notes leading to one main note."""
    for _ in range(count):
        motion=weighted_valid_motion(current_note, ORNAMENT_MOTIONS, ORNAMENT_MOTION_WEIGHTS, rng)
        current_note=apply_motion(current_note, motion, LOW_NOTE, HIGH_NOTE)
        append_note(events, current_note, THIRTY_SECOND, rng, "prefix ornament")
    return current_note


def generate_ending_ornaments(events, current_note, count, rng):
    """Create 0-2 32nd notes after the last main tone of a snippet."""
    for _ in range(count):
        motion=weighted_valid_motion(current_note, ORNAMENT_MOTIONS, ORNAMENT_MOTION_WEIGHTS, rng)
        current_note=apply_motion(current_note, motion, LOW_NOTE, HIGH_NOTE)
        append_note(events, current_note, THIRTY_SECOND, rng, "ending ornament")
    return current_note


def make_snippet(current_note, available_beats, rng):
    """Return one snippet and its final pitch.

    Every main-note value is a time container. Prefix ornaments are deducted
    from that value. On the final main-note container, ending ornaments are
    also deducted, so ornamentation never lengthens the phrase unexpectedly.
    """
    events=[]
    intended_count=rng.choices(SNIPPET_NOTE_COUNTS, weights=SNIPPET_NOTE_COUNT_WEIGHTS, k=1)[0]

    # Pre-select durations that fit. Stop early if near the composition ending.
    durations=[]
    remaining=available_beats
    for _ in range(intended_count):
        duration=choose_duration_that_fits(remaining, rng)
        if duration is None:
            break
        durations.append(duration)
        remaining-=duration
    if not durations:
        return [], current_note

    for index, container_duration in enumerate(durations):
        is_last=index==len(durations)-1
        prefix_count=rng.choices(PREFIX_ORNAMENT_COUNTS, weights=PREFIX_ORNAMENT_COUNT_WEIGHTS, k=1)[0]
        ending_count=(rng.choices(ENDING_ORNAMENT_COUNTS, weights=ENDING_ORNAMENT_COUNT_WEIGHTS, k=1)[0]
                      if is_last else 0)

        # Keep at least one 32nd-note unit for the main pitch itself.
        maximum_ornaments=max(0, int(round(container_duration / THIRTY_SECOND))-1)
        while prefix_count+ending_count > maximum_ornaments:
            if prefix_count >= ending_count and prefix_count > 0:
                prefix_count-=1
            elif ending_count > 0:
                ending_count-=1

        current_note=generate_prefix_ornaments(events,current_note,prefix_count,rng)

        # First main note uses the starting/current pitch. Later main notes make a florid Matrix motion.
        if index > 0 or prefix_count > 0:
            motion=weighted_valid_motion(current_note,MAIN_MOTIONS,MAIN_MOTION_WEIGHTS,rng)
            current_note=apply_motion(current_note,motion,LOW_NOTE,HIGH_NOTE)

        main_duration=container_duration-(prefix_count+ending_count)*THIRTY_SECOND
        append_note(events,current_note,main_duration,rng,"main")
        current_note=generate_ending_ornaments(events,current_note,ending_count,rng)

    return events,current_note


def generate_melody(rng):
    total_beats=MEASURES*TIME_SIGNATURE[0]*(4/TIME_SIGNATURE[1])
    events=[]
    elapsed=0.0
    current_note=START_NOTE

    while elapsed < total_beats-1e-9:
        remaining=total_beats-elapsed
        snippet,current_note=make_snippet(current_note,remaining,rng)
        if not snippet:
            events.append(("rest",remaining,None,None,"final rest"))
            break
        events.extend(snippet)
        elapsed+=sum(event[1] for event in snippet)

        remaining=total_beats-elapsed
        if remaining <= 1e-9:
            break
        rest_choices=[d for d in REST_DURATIONS if d <= remaining+1e-9]
        if rest_choices:
            corresponding=[REST_WEIGHTS[REST_DURATIONS.index(d)] for d in rest_choices]
            rest_duration=rng.choices(rest_choices,weights=corresponding,k=1)[0]
        else:
            rest_duration=remaining
        events.append(("rest",rest_duration,None,None,"snippet separation"))
        elapsed+=rest_duration

    return events

# -----------------------------------------------------------------------------
# STANDARD MIDI FILE WRITER
# -----------------------------------------------------------------------------
def variable_length(value):
    buffer=value & 0x7F
    output=bytearray([buffer])
    while value > 0x7F:
        value >>= 7
        output.insert(0,(value & 0x7F)|0x80)
    return bytes(output)


def meta_event(delta,event_type,data):
    return variable_length(delta)+bytes([0xFF,event_type])+variable_length(len(data))+data


def midi_message(delta,status,*data):
    return variable_length(delta)+bytes([status,*data])


def write_midi(events):
    numerator,denominator=TIME_SIGNATURE
    track=bytearray()
    track+=meta_event(0,0x03,b"Elegant Matrix Melody")
    mpq=round(60_000_000/TEMPO_BPM)
    track+=meta_event(0,0x51,mpq.to_bytes(3,"big"))
    track+=meta_event(0,0x58,bytes([numerator,int(math.log2(denominator)),24,8]))
    track+=midi_message(0,0xC0|CHANNEL,PROGRAM)

    pending=0
    for kind,beats,pitch,velocity,_role in events:
        ticks=round(beats*TICKS_PER_BEAT)
        if kind=="rest":
            pending+=ticks
        else:
            track+=midi_message(pending,0x90|CHANNEL,pitch,velocity)
            track+=midi_message(ticks,0x80|CHANNEL,pitch,0)
            pending=0
    track+=meta_event(pending,0x2F,b"")
    header=b"MThd"+struct.pack(">IHHH",6,0,1,TICKS_PER_BEAT)
    chunk=b"MTrk"+struct.pack(">I",len(track))+bytes(track)
    OUTPUT_FILE.write_bytes(header+chunk)
    return OUTPUT_FILE.resolve()


def validate(events):
    total=sum(event[1] for event in events)
    expected=MEASURES*TIME_SIGNATURE[0]*(4/TIME_SIGNATURE[1])
    if abs(total-expected)>1e-9:
        raise ValueError(f"Length is {total} beats, expected {expected}.")
    notes=[event for event in events if event[0]=="note"]
    if not notes:
        raise ValueError("No notes were generated.")
    if not all(LOW_NOTE<=e[2]<=HIGH_NOTE for e in notes):
        raise ValueError("A pitch is outside the active range.")
    if not all(VELOCITY_MIN<=e[3]<=VELOCITY_MAX for e in notes):
        raise ValueError("A velocity is outside the active range.")
    if not all(e[1]>0 for e in events):
        raise ValueError("All event durations must be positive.")


def main():
    rng=random.Random(RANDOM_SEED)
    events=generate_melody(rng)
    validate(events)
    output=write_midi(events)
    notes=[e for e in events if e[0]=="note"]
    roles={role:sum(e[4]==role for e in notes) for role in ("main","prefix ornament","ending ornament")}
    print("Created:",output)
    print("Length:",MEASURES,"measures in",f"{TIME_SIGNATURE[0]}/{TIME_SIGNATURE[1]}")
    print("Notes:",len(notes),roles)
    print("Pitch range used:",min(e[2] for e in notes),"to",max(e[2] for e in notes))


if __name__=="__main__":
    main()

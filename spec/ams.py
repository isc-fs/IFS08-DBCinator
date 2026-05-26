# SPDX-License-Identifier: proprietary
"""AMS-side wire contract.

Single source of truth for every frame the AMS firmware emits or
consumes on FDCAN1. Mirrors the layouts encoded in:
  - Core/Inc/app/telemetry_encoders.hpp  (0x4A0 / 0x4A1 / 0x4A2)
  - Core/Inc/app/acu_tx_encoders.hpp     (ECU TX matrix 0x020..0x137)
  - Core/Inc/app/pit_diag_emitter.hpp    (pit-diag 0x680..0x6C6)
  - Core/Src/app/can_isr.cpp             (RX dispatch -- 0x100, 0x002)
  - Core/Inc/app/ams_config.hpp          (ID constants)

When the firmware wire format changes, edit this file + regenerate
the artifacts under dist/. CI enforces sync.

Ported from IFS08-CE-AMS/tools/gen_dbc.py (which is now deprecated;
the firmware repo consumes from the spec-repo release).
"""

from __future__ import annotations

from .common import (
    BE, LE, BitField, Message, Signal,
    CELLS_PER_MOD, NTCS_PER_MOD, TOTAL_CELLS,
)


# --- Flight telemetry (SafetyTask @ 500 ms) --------------------------------

def status_4a0() -> Message:
    return Message(
        0x4A0, "AMS_status", 8, "AMS",
        comment="500 ms cadence. Top-level supervisor snapshot.",
        period_ms=500,
        signals=[
            Signal("fsm_state", LE(0, 8), unit="enum",
                   comment="See AMS_fsm_state enum below",
                   enum={0: "Start", 1: "Precharge", 2: "Transition",
                         3: "Run",   4: "Charge",    5: "Error"}),
            Signal("ams_ok", LE(1, 8), unit="bool",
                   comment="AMS_OK GPIO readback (PB4)"),
            Signal("module_online_mask", LE(2, 8),
                   minimum=0, maximum=31, unit="bitmask",
                   comment="Bit N set iff module N's last PEC-clean response "
                           "is within BmsStaleMs (#250)."),
            Signal("app_init_progress_or_reserved", LE(3, 8), unit="enum",
                   comment="HIL_STUB: g_app_init_progress (0..7); "
                           "flight: reserved=0"),
            Signal("min_cell_mV", BE(4, 16),
                   minimum=0, maximum=5000, unit="mV"),
            Signal("max_cell_mV", BE(6, 16),
                   minimum=0, maximum=5000, unit="mV"),
        ],
    )


def pack_4a1() -> Message:
    return Message(
        0x4A1, "AMS_pack", 8, "AMS",
        comment="500 ms cadence. Pack energy-state.",
        period_ms=500,
        signals=[
            Signal("pack_voltage_mV", LE(0, 32),
                   minimum=0, maximum=600_000, unit="mV"),
            Signal("filtered_mA", LE(4, 32, signed=True),
                   unit="mA", comment="+ discharge, − charge"),
        ],
    )


def temps_4a2() -> Message:
    return Message(
        0x4A2, "AMS_temps_diag", 8, "AMS",
        comment=("500 ms cadence. Bytes 3..4 carry dc_bus_V (flight) or "
                 "diag probes (HIL_STUB). Byte 5 is the always-on cockpit "
                 "byte (#251)."),
        period_ms=500,
        signals=[
            Signal("min_tempC", LE(0, 8, signed=True), unit="degC"),
            Signal("max_tempC", LE(1, 8, signed=True), unit="degC"),
            Signal("avg_tempC", LE(2, 8, signed=True), unit="degC"),
            Signal("dc_bus_V", LE(3, 16),
                   minimum=0, maximum=600, unit="V",
                   comment="Flight layout only. HIL_STUB overlays diag probes."),
            Signal("tsms_dash_chg_byte", LE(5, 8), unit="packed",
                   comment=("Cockpit snapshot (#251, always-on): bit7=sentinel, "
                            "bits3:2=mode_locked (0=Undecided/1=Car/2=Charger), "
                            "bit1=TSMS, bit0=DASH_CHG.")),
            Signal("tx_fail_count_lo", LE(6, 8), unit="count",
                   comment="Low byte of g_telemetry_tx_fail"),
            Signal("heartbeat", LE(7, 8), unit="count",
                   comment="500 ms wraparound counter; spot dropped frames"),
        ],
    )


# --- ECU TX matrix (AcuCanTask) --------------------------------------------

def ok_precharge_020() -> Message:
    return Message(
        0x020, "ECU_ok_precharge", 1, "AMS",
        comment="100 ms. 1 iff FSM in {Run, Charge}.",
        period_ms=100,
        signals=[Signal("ok_precharge", LE(0, 8), unit="bool")],
    )


def vmin_pack_12c() -> Message:
    return Message(
        0x12C, "ECU_v_cell_min", 2, "AMS",
        comment="100 ms. Pack-wide min cell voltage.",
        period_ms=100,
        signals=[Signal("v_cell_min_mV", BE(0, 16),
                        minimum=0, maximum=5000, unit="mV")],
    )


def vmod(can_id: int, mods: list, kind: str, suffix: str) -> Message:
    dlc = 2 * len(mods)
    sentinel = ("Sentinel 0xFFFF if module offline" if kind == "min"
                else "Sentinel 0x0000 if module offline")
    return Message(
        can_id, f"ECU_v{kind}_module_{suffix}", dlc, "AMS",
        comment=f"100 ms. Per-module v{kind} for modules {mods}.",
        period_ms=100,
        signals=[
            Signal(f"v{kind}_module_{mod}", BE(2 * i, 16),
                   minimum=0, maximum=5000, unit="mV",
                   comment=sentinel)
            for i, mod in enumerate(mods)
        ],
    )


def currents_135() -> Message:
    return Message(
        0x135, "ECU_currents", 4, "AMS",
        comment="50 ms (the fast lane). Pack + DCDC currents.",
        period_ms=50,
        signals=[
            Signal("pack_current_dA", BE(0, 16, signed=True),
                   factor=0.1, minimum=-825, maximum=825, unit="A",
                   comment="Signed deciamps (1 LSB = 0.1 A). + discharge."),
            Signal("dcdc_current_dA", BE(2, 16, signed=True),
                   factor=0.1, unit="A"),
        ],
    )


def tmax_mod(can_id: int, mods: list, suffix: str,
             include_dcdc: bool = False) -> Message:
    dlc = 2 * (len(mods) + (1 if include_dcdc else 0))
    sigs = [
        Signal(f"tmax_module_{mod}", BE(2 * i, 16, signed=True),
               unit="degC", comment="Sentinel INT16_MIN if offline")
        for i, mod in enumerate(mods)
    ]
    if include_dcdc:
        sigs.append(Signal("temp_dcdc",
                           BE(2 * len(mods), 16, signed=True),
                           unit="degC",
                           comment="DCDC temperature stub (currently 0)"))
    return Message(
        can_id, f"ECU_tmax_module_{suffix}", dlc, "AMS",
        comment=f"250 ms. Per-module max temp for modules {mods}.",
        period_ms=250,
        signals=sigs,
    )


# --- External RX -----------------------------------------------------------

def vcu_100() -> Message:
    return Message(
        0x100, "VCU_dc_bus_heartbeat", 2, "VCU",
        comment="20 Hz from VCU. AMS consumes for mode-lock + DC bus.",
        period_ms=50,
        signals=[Signal("dc_bus_V", LE(0, 16),
                        minimum=0, maximum=600, unit="V")],
    )


def bl_trigger_002() -> Message:
    return Message(
        0x002, "BL_boot_trigger", 4, "Pit_Tool",
        comment=("Reboot AMS into bootloader. Payload must be magic "
                 "0xB007AD11. Handled by AcuCanTask before VCU dispatch."),
        signals=[Signal("magic", BE(0, 32), unit="magic",
                        comment="Must equal 0xB007AD11")],
    )


# --- Pit-diag enable / ACK --------------------------------------------------

def pit_cmd_7f0() -> Message:
    return Message(
        0x7F0, "PitDiag_cmd", 4, "Pit_Tool",
        comment=("Enable/disable the pit-diag stream (#247). 4-byte "
                 "payload = 0xDEADBEEF enable / 0x00000000 disable."),
        signals=[Signal("cmd_magic", BE(0, 32), unit="magic",
                        comment="0xDEADBEEF enable, 0x00000000 disable")],
    )


def pit_ack_7f1() -> Message:
    return Message(
        0x7F1, "PitDiag_ack", 1, "AMS",
        comment="One-shot ACK after a 0x7F0 state transition.",
        signals=[Signal("enabled", LE(0, 8), unit="bool",
                        comment="1 if stream just enabled, 0 if disabled")],
    )


# --- Pit-diag cell / temp grids --------------------------------------------

def pit_cells() -> list:
    """24 frames covering all 95 cells (4 cells/frame, BE u16 mV)."""
    out = []
    for frame_idx in range(24):
        sigs = []
        for slot in range(4):
            cell_index = 4 * frame_idx + slot
            if cell_index < TOTAL_CELLS:
                mod, cell = divmod(cell_index, CELLS_PER_MOD)
                name = f"cell_m{mod}_c{cell:02d}_mV"
                cmt = f"Module {mod}, cell {cell}"
            else:
                name = f"sentinel_slot{slot}"
                cmt = "Beyond cell 94: always 0xFFFF"
            sigs.append(Signal(name, BE(2 * slot, 16),
                               minimum=0, maximum=5000, unit="mV",
                               comment=cmt))
        out.append(Message(
            0x680 + frame_idx, f"PitDiag_cells_{frame_idx:02d}",
            8, "AMS",
            comment=("Pit-diag cell-V frame. Decode: "
                     "cell_index = 4*frame_idx + slot; "
                     "module = cell_index // 19; cell = cell_index % 19. "
                     "0xFFFF in any slot = no cell at that index."),
            period_ms=1000,
            condition="pit_diag_enabled",
            signals=sigs,
        ))
    return out


def pit_temps() -> list:
    """25 frames covering all 200 NTCs (8 NTCs/frame, i8 degC)."""
    out = []
    for frame_idx in range(25):
        sigs = []
        for slot in range(8):
            temp_index = 8 * frame_idx + slot
            mod, temp = divmod(temp_index, NTCS_PER_MOD)
            sigs.append(Signal(
                f"temp_m{mod}_t{temp:02d}_C",
                LE(slot, 8, signed=True),
                unit="degC", comment=f"Module {mod}, NTC {temp}"))
        out.append(Message(
            0x6A0 + frame_idx, f"PitDiag_temps_{frame_idx:02d}",
            8, "AMS",
            comment=("Pit-diag NTC-temp frame. Decode: "
                     "temp_index = 8*frame_idx + slot; "
                     "module = temp_index // 40; temp = temp_index % 40."),
            period_ms=1000,
            condition="pit_diag_enabled",
            signals=sigs,
        ))
    return out


# --- Pit-diag scalar frames -------------------------------------------------

def pit_fsm_status_6c0() -> Message:
    return Message(
        0x6C0, "PitDiag_fsm_status", 8, "AMS",
        comment="FSM extended status. 1 Hz when pit-diag enabled.",
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("fsm_state", LE(0, 8), unit="enum",
                   enum={0: "Start", 1: "Precharge", 2: "Transition",
                         3: "Run", 4: "Charge", 5: "Error"}),
            Signal("mode_locked", LE(1, 8), unit="enum",
                   enum={0: "Undecided", 1: "Car", 2: "Charger"}),
            Signal("tsms_readback",     BitField(16, 1, "1", "+"), unit="bool"),
            Signal("dash_chg_readback", BitField(17, 1, "1", "+"), unit="bool"),
            Signal("ams_ok", LE(3, 8), unit="bool"),
            Signal("pec_err_total", BE(4, 16), unit="count",
                   comment="Sum of g_ltc_pec_err_count[10]; saturates 0xFFFF"),
        ],
    )


def pit_timing_6c1() -> Message:
    return Message(
        0x6C1, "PitDiag_timing", 8, "AMS",
        comment="V-poll cadence + last temp-sweep failure mask.",
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("bms_volt_poll_ms",     BE(0, 16), unit="ms"),
            Signal("bms_volt_poll_max_ms", BE(2, 16), unit="ms"),
            Signal("temp_sweep_last_mask", LE(4, 32), unit="bitmask",
                   comment="1 bit per NTC channel that failed the last sweep"),
        ],
    )


def pit_balance_a_6c2() -> Message:
    return Message(
        0x6C2, "PitDiag_balance_mask_a", 8, "AMS",
        comment=("Balance DCC bits for cell indices 0..63. Bit b of byte i "
                 "represents cell (8*i + b). Decode: module = cell_idx // 19, "
                 "cell = cell_idx % 19."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[Signal("dcc_bits_lo64", LE(0, 64), unit="bitmask")],
    )


def pit_balance_b_6c3() -> Message:
    return Message(
        0x6C3, "PitDiag_balance_mask_b", 8, "AMS",
        comment=("Balance DCC bits for cell indices 64..94 + per-cycle "
                 "counters. Bits 64..94 occupy bytes 0..3 (low 31 bits)."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("dcc_bits_hi32", LE(0, 32), unit="bitmask",
                   comment="Low 31 bits = cells 64..94; bit 31 reserved 0"),
            Signal("balance_cycles_total",  LE(4, 16), unit="count",
                   comment="Mod 65536"),
            Signal("balance_cycles_active", LE(6, 16), unit="count",
                   comment="Cycles where at least one DCC bit was set"),
        ],
    )


def pit_boot_diag_6c4() -> Message:
    return Message(
        0x6C4, "PitDiag_boot_diag", 8, "AMS",
        comment="Reset reason + App_InitTask milestone + FDCAN1 start result.",
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("jump_reason", LE(0, 32), unit="enum",
                   enum={0:          "ColdPOR",
                         0x4A554D50: "CanTrigger",
                         0x4D414E55: "ManualRequest"}),
            Signal("app_init_progress", LE(4, 8), unit="enum",
                   comment="0..7 milestone counter, 7=clean self-exit"),
            Signal("fdcan1_start_result", LE(5, 24), unit="HAL_StatusTypeDef",
                   comment="0=HAL_OK; low 24 bits of HAL_FDCAN_Start return"),
        ],
    )


def pit_post_mortem_6c5() -> Message:
    return Message(
        0x6C5, "PitDiag_post_mortem", 8, "AMS",
        comment=("FreeRTOS stack-overflow + malloc-failed hooks "
                 "(captured to .bss; survives soft reset)."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("stack_overflow_seen", LE(0, 8), unit="bool",
                   comment="1 if g_stack_overflow_task_addr != 0"),
            Signal("watermark_low_byte", LE(1, 8), unit="words",
                   comment="Saturates at 0xFF"),
            Signal("task_addr_lo", LE(2, 32), unit="address",
                   comment="Low 32 bits of failing task's xTaskHandle"),
            Signal("malloc_failed_count", LE(6, 16), unit="count",
                   comment="Saturates at 0xFFFF"),
        ],
    )


def pit_pec_per_ic_a_6c7() -> Message:
    """Per-IC PEC error count for the first 8 LTC6811s in the chain
    (modules 0..3, both ICs each). Saturating u8 per IC -- a sustained
    spike on byte N == "chain index N is misbehaving"."""
    return Message(
        0x6C7, "PitDiag_pec_per_ic_a", 8, "AMS",
        comment=("Per-IC PEC error count, ICs 0..7. Each byte is a "
                 "saturating u8 capped at 0xFF. Decode: byte i = "
                 "g_ltc_pec_err_count[i] saturated. Modules: ic 0,1 = "
                 "module 0; ic 2,3 = module 1; ic 4,5 = module 2; "
                 "ic 6,7 = module 3."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal(f"pec_err_ic_{i}", LE(i, 8), unit="count",
                   comment=f"Chain IC {i} (module {i // 2}, "
                           f"{'upper' if i % 2 == 0 else 'lower'} LTC6811)")
            for i in range(8)
        ],
    )


def pit_pec_per_ic_b_6c8() -> Message:
    """Per-IC PEC error count for the last 2 LTC6811s (module 4) plus
    reserved bytes."""
    return Message(
        0x6C8, "PitDiag_pec_per_ic_b", 8, "AMS",
        comment=("Per-IC PEC error count, ICs 8..9 + 6 reserved bytes. "
                 "Same encoding as 0x6C7."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("pec_err_ic_8", LE(0, 8), unit="count",
                   comment="Chain IC 8 (module 4, upper LTC6811)"),
            Signal("pec_err_ic_9", LE(1, 8), unit="count",
                   comment="Chain IC 9 (module 4, lower LTC6811)"),
        ],
    )


def pit_fw_id_6c6() -> Message:
    return Message(
        0x6C6, "PitDiag_fw_id", 8, "AMS",
        comment=("Firmware identification (post-IFS08-CE-AMS#252). Populated "
                 "from VERSION file + git rev-parse at configure time."),
        period_ms=1000,
        condition="pit_diag_enabled",
        signals=[
            Signal("fw_version_major", LE(0, 8), unit="semver"),
            Signal("fw_version_minor", LE(1, 8), unit="semver"),
            Signal("fw_version_patch", LE(2, 8), unit="semver"),
            Signal("git_hash_0", LE(3, 8), unit="byte"),
            Signal("git_hash_1", LE(4, 8), unit="byte"),
            Signal("git_hash_2", LE(5, 8), unit="byte"),
            Signal("git_hash_3", LE(6, 8), unit="byte"),
            Signal("bl_node_id", LE(7, 8), unit="id",
                   comment="From firmware_info.reserved[0]; pit tool checks "
                           "against the BL"),
        ],
    )


# --- Aggregate -------------------------------------------------------------

MESSAGES = [
    # SafetyTask telemetry
    status_4a0(), pack_4a1(), temps_4a2(),
    # ECU TX matrix
    ok_precharge_020(), vmin_pack_12c(),
    vmod(0x131, [0, 1, 2], "min", "A"),
    vmod(0x132, [3, 4],    "min", "B"),
    vmod(0x133, [0, 1, 2], "max", "A"),
    vmod(0x134, [3, 4],    "max", "B"),
    currents_135(),
    tmax_mod(0x136, [0, 1, 2], "A"),
    tmax_mod(0x137, [3, 4],    "B", include_dcdc=True),
    # External RX
    vcu_100(), bl_trigger_002(),
    # Pit-diag enable / ACK
    pit_cmd_7f0(), pit_ack_7f1(),
    # Pit-diag stream
    *pit_cells(),
    *pit_temps(),
    pit_fsm_status_6c0(),
    pit_timing_6c1(),
    pit_balance_a_6c2(),
    pit_balance_b_6c3(),
    pit_boot_diag_6c4(),
    pit_post_mortem_6c5(),
    pit_fw_id_6c6(),
    pit_pec_per_ic_a_6c7(),
    pit_pec_per_ic_b_6c8(),
]

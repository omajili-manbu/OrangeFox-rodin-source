#!/system/bin/sh

# rodin haptics: prefer the AAC/ICS rt6010 motor at i2c 0-005f served from
# vendor_dlkm, falling back to the SIH6887 path at 0-006b on hardware
# variants whose DT carries the SIH device instead. The AAC driver pulls
# haptic_config.bin through request_firmware about eight seconds after
# probe; the recovery ramdisk ships it under /vendor/firmware together with
# the preset RTP waveforms, and the mounted vendor partition carries the
# same set.

MODULE_DIR="/vendor_dlkm/lib/modules"
AAC_NODE="/sys/bus/i2c/devices/0-005f"
SIH_NODE="/sys/bus/i2c/drivers/sih_haptic_688X/0-006b"

setprop vendor.haptics.ready 0

# Touch is already ready before this service starts.
# Allow a brief settling delay instead of waiting 20 seconds.
sleep 3

if [ ! -e "$MODULE_DIR/aac_haptic.ko" ]; then
    mount -t erofs -o ro /dev/block/mapper/vendor_dlkm_a /vendor_dlkm 2>/dev/null ||
        mount -o ro /dev/block/mapper/vendor_dlkm_a /vendor_dlkm 2>/dev/null
fi

# AAC/ICS rt6010 first. The driver only probes when the DT carries the ics
# motor; a failed probe leaves the sysfs node absent and falls through to
# the SIH path. Never unload: this kernel's module removal path is unsafe.
if [ -d "$AAC_NODE" ]; then
    if ! grep -q '^aac_haptic ' /proc/modules; then
        insmod "$MODULE_DIR/aac_haptic.ko" 2>/dev/null
    fi
    if [ -e "$AAC_NODE/activate" ]; then
        # f0 turns non-zero once the delayed chip config work has consumed
        # haptic_config.bin; the rtp presets are unusable before that.
        tries=0
        while [ "$tries" -lt 300 ]; do
            f0="$(cat "$AAC_NODE/f0" 2>/dev/null)"
            case "$f0" in
                ''|0) ;;
                *)
                    setprop vendor.haptics.ready 1
                    exit 0
                    ;;
            esac
            sleep 0.1
            tries=$((tries + 1))
        done
        echo "rt6010 chip config did not become ready" >&2
        exit 2
    fi
fi

# SIH6887 fallback (hardware variants without the ics motor).
if ! grep -q '^si_haptic ' /proc/modules; then
    insmod /lib/modules/si_haptic.ko || {
        echo "Failed to load si_haptic.ko" >&2
        exit 1
    }
fi

tries=0

while [ "$tries" -lt 100 ]; do
    if [ -e "$SIH_NODE/activate" ] &&
       [ -e "$SIH_NODE/duration" ] &&
       [ -e "$SIH_NODE/gain" ] &&
       [ -e "$SIH_NODE/ram_num" ]; then

        ram_status="$(cat "$SIH_NODE/ram_num" 2>/dev/null)"

        case "$ram_status" in
            *"wave_num = "[1-9]*)
                # 64 decimal = 0x40, stronger than stock recovery's 0x1e.

                setprop vendor.haptics.ready 1
                exit 0
                ;;
        esac
    fi

    sleep 0.1
    tries=$((tries + 1))
done

echo "SIH6887 RAM firmware did not become ready" >&2
exit 2

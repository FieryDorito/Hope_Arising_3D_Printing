// Hose / drip line end plug
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Pushes into the end of a drip irrigation line or garden hose to close it.
// The loop lets you hang it on a nail or tie it so it is not lost.
// Prints cap down, no supports. Use PETG (better with water and sun).

/* [Size] */
// Inside diameter of the hose or drip line
hose_inner_diameter = 14;  // [3:0.5:30]
// Number of barbs. More barbs hold tighter.
barb_count = 2;            // [1:1:4]

/* [Options] */
// Add a loop to hang or tie the plug
add_loop = 1;              // [0:No, 1:Yes]

/* [Hidden] */
$fn = 96;

module hose_end_plug() {
    r  = hose_inner_diameter / 2;
    h  = max(0.6, 0.08 * hose_inner_diameter);
    l  = max(3, 0.35 * hose_inner_diameter);
    cap_r = r + h + 2.5;
    cap_t = 3;
    n = barb_count;
    // solid barbed stem standing on the cap
    profile = concat(
        [[0, 0], [cap_r, 0], [cap_r, cap_t], [r, cap_t]],
        [for (i = [0 : n - 1]) each [[r + h, cap_t + i * (h + l) + h], [r, cap_t + (i + 1) * (h + l)]]],
        [[r * 0.6, cap_t + n * (h + l) + r * 0.4], [0, cap_t + n * (h + l) + r * 0.4]]
    );
    rotate_extrude() polygon(profile);
    if (add_loop == 1)
        translate([cap_r + 3, 0, 0])
            difference() {
                hull() {
                    cylinder(h = cap_t, r = 5);
                    translate([-4, 0, 0]) cylinder(h = cap_t, r = 5);
                }
                translate([0.5, 0, -1]) cylinder(h = cap_t + 2, r = 2);
            }
}

hose_end_plug();

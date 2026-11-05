// Hose barb connector / reducer
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Joins two hoses. Use the same size on both ends to repair a cut hose,
// or two sizes to connect different hoses. Add a hose clamp for water under pressure.
// Prints standing up, no supports. Use PETG for water.

/* [Size] */
// Inside diameter of the first hose (bottom end)
hose_a_inner_diameter = 16;   // [4:0.5:40]
// Inside diameter of the second hose (top end)
hose_b_inner_diameter = 12;   // [4:0.5:40]
// Number of barbs on each end. More barbs grip better.
barb_count = 3;               // [1:1:5]
// Wall thickness of the tube. Thicker is stronger but lets less water through.
wall_thickness = 1.6;         // [1.2:0.2:4]

/* [Hidden] */
$fn = 96;

function rise(d) = max(0.6, 0.08 * d);       // how far each barb sticks out
function blen(d) = max(3, 0.35 * d);         // length of each barb

module hose_barb_reducer() {
    ra = hose_a_inner_diameter / 2;
    rb = hose_b_inner_diameter / 2;
    ha = rise(hose_a_inner_diameter);  la = blen(hose_a_inner_diameter);
    hb = rise(hose_b_inner_diameter);  lb = blen(hose_b_inner_diameter);
    n  = barb_count;
    rc = max(ra + ha, rb + hb) + 1.5;          // collar radius
    za = n * la;                                // top of bottom barbs
    // collar: 45 degree underside so it prints without support
    zc0 = za;  zc1 = za + (rc - ra);  zc2 = zc1 + 3;
    // top barbs: 45 degree out, then taper in toward the tip
    zb = zc2;
    bore_a = max(0.75, ra - wall_thickness);
    bore_b = max(0.75, rb - wall_thickness);
    ztop = zb + n * (hb + lb);
    outer = concat(
        [[ra, 0]],
        [for (i = [0 : n - 1]) each [[ra + ha, (i + 1) * la], [ra, (i + 1) * la]]],
        [[rc, zc1], [rc, zc2], [rb, zc2]],
        [for (i = [0 : n - 1]) each [[rb + hb, zb + i * (hb + lb) + hb], [rb, zb + (i + 1) * (hb + lb)]]]
    );
    // 45 degree change between the two bore sizes, kept inside the wall:
    // widening -> starts where the collar starts; narrowing -> ends at the top of the collar
    dz = abs(bore_a - bore_b);
    inner = bore_b >= bore_a
        ? [[bore_b, ztop], [bore_b, za + dz], [bore_a, za], [bore_a, 0]]
        : [[bore_b, ztop], [bore_b, zc2], [bore_a, max(0, zc2 - dz)], [bore_a, 0]];
    rotate_extrude() polygon(concat(outer, inner));
}

hose_barb_reducer();

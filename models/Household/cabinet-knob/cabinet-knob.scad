// Cabinet / drawer knob
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Replaces a lost or broken knob on a drawer, cabinet or door.
// A screw goes through the door into the knob; a nut can be pushed into the base.
// Prints base down, no supports.

/* [Size] */
// Diameter of the knob head
knob_diameter = 30;        // [15:1:70]
// Total height of the knob
knob_height = 25;          // [12:1:60]
// Diameter of the screw that holds it on (3 = M3, 4 = M4, 5 = M5)
screw_diameter = 4;        // [2.5:0.5:6]

/* [Options] */
// Pocket in the base for a nut (use with a machine screw)
nut_pocket = 1;            // [0:No, 1:Yes]

/* [Hidden] */
$fn = 96;

function nut_flats(d) = d <= 3.2 ? 5.5 : d <= 4.2 ? 7 : d <= 5.2 ? 8 : 10;

module cabinet_knob() {
    nut_w = nut_flats(screw_diameter) + 0.4;
    nut_h = 0.8 * screw_diameter + 0.4;
    hr = knob_diameter / 2;
    nr = max(nut_w / 2 / cos(30) + 2, min(hr * 0.55, hr - 3));   // neck radius
    flare = hr - nr;                                            // 45 degree flare
    neck_h = max(3, knob_height - flare - 6);
    total = max(knob_height, neck_h + flare + 3);
    top_h = total - neck_h - flare;                             // height of the head
    rr = min(4, top_h / 2);                                     // rounded top edge
    difference() {
        rotate_extrude() union() {
            square([nr, neck_h + 0.01]);
            translate([0, neck_h]) polygon([[0, 0], [nr, 0], [hr, flare], [0, flare]]);
            translate([0, neck_h + flare - 0.01]) hull() {
                square([hr, 0.01]);
                square([0.01, top_h]);
                translate([hr - rr, top_h - rr]) circle(r = rr);
            }
        }
        // screw hole
        translate([0, 0, -1]) cylinder(h = min(total - 3, neck_h + flare) + 1, d = screw_diameter + 0.3, $fn = 32);
        // nut pocket at the base
        if (nut_pocket == 1)
            translate([0, 0, -1]) cylinder(h = nut_h + 1, r = nut_w / 2 / cos(30), $fn = 6);
    }
}

cabinet_knob();

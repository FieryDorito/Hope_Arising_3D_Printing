// Spacer / washer
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Prints flat, no supports. Use for spacing out shelves, wheels, brackets and fan mounts.

/* [Size] */
// Outside diameter of the spacer (across the flats for a hexagon)
outer_diameter = 20;      // [5:0.5:100]
// Diameter of the bolt or rod that goes through it. A little extra is added so it fits.
bolt_diameter = 8;        // [1:0.1:80]
// Height (thickness) of the spacer
height = 5;               // [0.6:0.2:100]

/* [Shape] */
// Outside shape
shape = 0;                // [0:Round, 1:Hexagon]

/* [Hidden] */
$fn = 96;
hole_clearance = 0.3;     // printed holes come out slightly small
min_wall = 1.2;

module spacer() {
    hole_d = bolt_diameter + hole_clearance;
    od = max(outer_diameter, hole_d + 2 * min_wall);
    difference() {
        if (shape == 1)
            cylinder(h = height, r = od / 2 / cos(30), $fn = 6);
        else
            cylinder(h = height, d = od);
        translate([0, 0, -1]) cylinder(h = height + 2, d = hole_d);
    }
}

spacer();

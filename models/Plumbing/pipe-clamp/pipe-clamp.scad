// Pipe clamp (wall saddle)
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Holds a pipe, hose or cable bundle against a wall with two screws.
// Prints lying on its side, no supports. Use the Strong setting for heavy pipes.

/* [Size] */
// Outside diameter of the pipe it holds
pipe_diameter = 25;       // [6:0.5:110]
// Width of the strap (how far along the pipe it reaches)
strap_width = 15;         // [6:1:40]
// Thickness of the strap. Thicker is stronger.
strap_thickness = 3;      // [2:0.5:8]
// Diameter of the wall screws
screw_diameter = 4;       // [2.5:0.5:6]

/* [Hidden] */
$fn = 128;
fit_clearance = 0.4;      // so the pipe slides in

module pipe_clamp() {
    r  = (pipe_diameter + fit_clearance) / 2;
    t  = strap_thickness;
    hole_d = screw_diameter + 0.3;
    ear = max(hole_d * 2.6, 8);                 // flange length past the strap
    ear_x = r + t + ear / 2;                    // screw hole position
    cy = r - 0.5;                               // pipe pressed 0.5 mm into the wall line = grip
    difference() {
        linear_extrude(height = strap_width)
            difference() {
                union() {
                    // U strap over the pipe, cut off at the wall (y = 0)
                    intersection() {
                        translate([0, cy]) circle(r = r + t);
                        translate([-(r + t + 1), 0]) square([2 * (r + t + 1), 2 * (r + t) + 1]);
                    }
                    // flat flange against the wall
                    translate([-(r + t + ear), 0]) square([2 * (r + t + ear), t]);
                }
                translate([0, cy]) circle(r = r);
            }
        // screw holes through the flange, into the wall
        for (sx = [-1, 1])
            translate([sx * ear_x, -1, strap_width / 2])
                rotate([-90, 0, 0]) cylinder(h = t + 2, d = hole_d, $fn = 32);
    }
}

pipe_clamp();

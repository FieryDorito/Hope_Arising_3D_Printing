// Pipe end cap
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Slides over the end of a pipe, tube, furniture leg or rod to cover it.
// NOT for water pressure: it covers and protects, it does not seal a pressurised line.
// Prints closed end down, no supports.

/* [Size] */
// Outside diameter of the pipe or leg it fits over
pipe_diameter = 25;       // [5:0.5:110]
// How far the cap slides onto the pipe
cap_depth = 15;           // [5:1:50]
// Thickness of the cap's side and end
wall_thickness = 2;       // [1.2:0.2:5]

/* [Hidden] */
$fn = 128;
fit_clearance = 0.3;      // snug push fit

module pipe_end_cap() {
    id = pipe_diameter + fit_clearance;
    od = id + 2 * wall_thickness;
    difference() {
        cylinder(h = cap_depth + wall_thickness, d = od);
        translate([0, 0, wall_thickness]) cylinder(h = cap_depth + 1, d = id);
        // small lead-in chamfer at the open end so it starts onto the pipe easily
        translate([0, 0, cap_depth + wall_thickness - 0.8])
            cylinder(h = 0.81, d1 = id, d2 = id + 1.2);
    }
}

pipe_end_cap();

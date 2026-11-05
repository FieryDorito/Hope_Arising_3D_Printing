// Drill guide jig
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Hold it on the work and drill through the hole to get a straight, centred hole.
// The side notches line up with pencil marks. The V-groove sits on round rods and pipes.
// Plastic wears with use: fine for a few dozen holes, not for production drilling.
// Prints flat side down, no supports. Use the Strong setting.

/* [Size] */
// Diameter of the drill bit
drill_diameter = 6;        // [2:0.5:13]
// Height of the guide. Taller keeps the drill straighter.
guide_height = 25;         // [10:1:50]
// Width of the block
block_width = 30;          // [20:1:70]

/* [Options] */
// Bottom shape
bottom = 0;                // [0:Flat, 1:V-groove for round rods]

/* [Hidden] */
$fn = 64;
hole_clearance = 0.3;
notch = 1.5;

module drill_guide_jig() {
    w = max(block_width, drill_diameter + 12);
    h = guide_height;
    groove = min(w * 0.35, h * 0.4);       // depth of the V-groove
    difference() {
        translate([-w / 2, -w / 2, 0]) cube([w, w, h]);
        // guide hole
        translate([0, 0, -1]) cylinder(h = h + 2, d = drill_diameter + hole_clearance);
        // centre notches on the four sides, full height
        for (a = [0 : 90 : 270]) rotate(a)
            translate([w / 2, 0, h / 2]) rotate(45) cube([notch * 1.414, notch * 1.414, h + 2], center = true);
        // V-groove across the bottom (45 degree sides print without support)
        if (bottom == 1)
            translate([0, 0, 0]) rotate([90, 0, 0])
                linear_extrude(height = w + 2, center = true)
                    polygon([[-groove, -1], [groove, -1], [0, groove]]);
    }
}

drill_guide_jig();

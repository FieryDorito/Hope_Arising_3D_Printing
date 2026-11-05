// Plant stake clip
// Hope Arising EPICS team (ASU) - original design. License: CC BY 4.0 (commercial use allowed).
// Snaps onto a stake or bamboo pole and holds a plant stem loosely beside it
// (tomatoes, peppers, beans, young trees). Prints flat, no supports.

/* [Size] */
// Diameter of the stake or pole
stake_diameter = 12;       // [4:0.5:40]
// Diameter of the plant stem. The loop is made a little bigger so the stem can grow.
stem_diameter = 10;        // [3:0.5:40]
// Height of the clip (how tall it prints)
clip_height = 8;           // [4:1:20]
// Thickness of the clip ring. Thinner snaps on more easily.
wall_thickness = 2;        // [1.2:0.2:4]

/* [Hidden] */
$fn = 96;

// ring with an opening of 'gap' width facing direction 'ang'
module c_ring(r_in, t, gap, ang) {
    difference() {
        circle(r = r_in + t);
        circle(r = r_in);
        rotate(ang) translate([0, -gap / 2]) square([r_in + t + 1, gap]);
    }
}

module plant_stake_clip() {
    rs = stake_diameter / 2 + 0.2;          // snap fit on the stake
    rp = stem_diameter / 2 + 2;             // loose loop for the stem
    t  = wall_thickness;
    cx = rs + t + rp + t + 2;               // distance between ring centres
    linear_extrude(height = clip_height) {
        c_ring(rs, t, stake_diameter * 0.75, 180);              // stake: opening faces away
        translate([cx, 0]) c_ring(rp, t, stem_diameter * 0.6, 60);  // stem: opening to the side
        // bridge joining the two rings
        translate([rs + t / 2, -t]) square([cx - rs - rp - t, 2 * t]);
    }
}

plant_stake_clip();

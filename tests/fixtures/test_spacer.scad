// Test fixture: parametric round spacer (not a real library part)
outer_diameter = 20;
inner_diameter = 8;
height = 10;
$fn = 64;
difference() {
  cylinder(d = outer_diameter, h = height);
  translate([0, 0, -1]) cylinder(d = inner_diameter, h = height + 2);
}

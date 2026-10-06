struct Affine2 {
    matrix2: Mat2,
    translation: Vec2,
}

impl Affine2 {
    #[inline]
    fn inverse(&self) {
        mat2_ref(&self.matrix2);
        vec2_move(self.translation);
    }
}

#[derive(Clone, Copy)]
#[repr(align(8))]
struct Mat2([f32; 4]);

#[derive(Clone, Copy)]
struct Vec2 {
    x: f32, 
    y: f32, 
}

#[inline(never)]
fn mat2_ref(_: &Mat2) {
    loop {}
}

#[inline(never)]
fn vec2_move(_: Vec2) {
    loop {}
}

fn main() {
    Affine2 {
        matrix2: Mat2([0.0, 0.0, 0.0, 0.0]),
        translation: Vec2 { x: 0.0, y: 0.0 },
    }.inverse();
}
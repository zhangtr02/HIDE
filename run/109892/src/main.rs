trait MyCmp {
    fn cmp(&self) {}
}
impl MyCmp for f32 {}

fn main() {
    0.0.cmp();
}
fn higher_ranked(ctx: &mut ()) {}

fn main() {
    fn as_unsafe<T>(_: unsafe fn(T)) {}
    as_unsafe(higher_ranked);
}

fn foo(b: bool) -> impl IntoIterator<Item = ()> {
    if b {
        return vec![()];
    }
    [()].into_iter().collect()
}

fn main() {}
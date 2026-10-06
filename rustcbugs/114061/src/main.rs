use dep::*;

struct Local;
impl WithAssoc<'_> for Box<Local> {
    type Assoc = ();
}

fn impls_trait<T: Trait>() {}

fn main() {
    impls_trait::<Box<Local>>();
}
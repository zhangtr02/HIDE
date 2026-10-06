trait Array {
    type Item;
}

trait Platform {
    type Assoc: Array<Item = Self::Assoc2>;
    type Assoc2;
}

struct Message<A, B>
where
    A: Array<Item = B>,
{
    pub field: A,
}

impl<A, B> Clone for Message<A, B>
where
    A: Array<Item = B>,
{
    fn clone(&self) -> Self {
        todo!()
    }
}

fn clone<P: Platform>(x: &Message<P::Assoc, P::Assoc2>) {
    let x: Message<_, _> = Clone::clone(x);
}

fn main() {}
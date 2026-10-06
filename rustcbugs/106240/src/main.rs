struct Foo<T, const N: usize> {
    array: [T; N],
}

trait Bar<const N: usize> {}

impl<T, const N: usize> Foo<T, N> {
    fn trigger(self) {
        self.unsatisfied()
        //   ^^^^^^^^^^^ expected location of type error (T: Bar<N> isn't satisfied)
        // Instead, nightly produces an ICE when
    }

    fn unsatisfied(self)
    where
        T: Bar<N>,
    {
    }
}

fn main() {}
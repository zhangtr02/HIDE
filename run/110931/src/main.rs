fn foo() -> [(); {
    let a = 10_usize;
    let b: &'_ usize = &a;
    *b
}] {
    
}

fn main() {}
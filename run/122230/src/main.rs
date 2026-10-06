struct Test {
    field: Vec<&'static str>
}

impl Test {
    fn field<'field>(&self) -> impl Iterator<Item = &&'field str> {
        self.field.iter()
    }
}

fn main() {}
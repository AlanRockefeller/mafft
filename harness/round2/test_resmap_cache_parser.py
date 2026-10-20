"""Pure Python fixtures; importing the generator never runs main or a compiler."""

import unittest

from resmap_cache_check import (
    function, function_parameters, mask_c_comments, null_arguments,
)


class ResidueMapParserTests(unittest.TestCase):
    def test_comments_in_signature_and_between_signature_and_body(self):
        original = (
            "double A__align( double **matrix /* ; { }, ) ( , fake */,\n"
            " int n /* , ; }) */, char **seq1 )\n"
            "/* score note ; {}, fake(thing, other) */\n"
            "{\n\treturn( 0.0 );\n}"
        )
        source = "/* prefix \xe9 ; {} */\n" + original + "\nint unrelated;\n"
        start, end, extracted = function(source, "A__align")
        self.assertEqual(extracted, original)
        self.assertEqual(source[start:end].encode("latin-1"), original.encode("latin-1"))
        self.assertEqual(null_arguments(source, "A__align"), ["NULL", "0", "NULL"])
        self.assertEqual(len(function_parameters(source, "A__align")), 3)

    def test_comments_are_blanked_but_literals_and_offsets_are_preserved(self):
        quoted = r'"/* ;{}(), // */ \"still a string\""'
        character = r"'/'"
        source = quoted + " /* ;{}, ()\n * comment */ " + character + " // trailing { ; ,\r\n"
        masked = mask_c_comments(source)
        self.assertEqual(len(masked), len(source))
        self.assertEqual(masked[:len(quoted)], quoted)
        position = source.index(character)
        self.assertEqual(masked[position:position + len(character)], character)
        self.assertEqual([i for i, c in enumerate(masked) if c in "\r\n"],
                         [i for i, c in enumerate(source) if c in "\r\n"])
        self.assertNotIn("trailing", masked)
        self.assertNotIn("comment", masked)

    def test_literals_with_comment_tokens_and_braces_do_not_end_function(self):
        original = r'''static int sample( int n /* ; { } , ( ) */ )
{
    const char *a = "/* } , ; ) */ // {";
    const char *b = "\"//\" and '\\' /* literal */";
    char slash = '/', quote = '\'', brace = '}';
    /* } fake close ; ( , */
    // { fake opening ; ) ,
    if( n ) { return( 1 ); }
    return( 0 );
}'''
        source = original + "\nint next;\n"
        self.assertEqual(function(source, "sample")[2], original)
        self.assertEqual(null_arguments(source, "sample"), ["0"])

    def test_prototypes_and_commented_definitions_are_ignored(self):
        original = "int chosen( int x ) /* ;{}(), */\n{\n return x;\n}"
        source = (
            "/* int chosen( int ignored ) { return 2; } */\n"
            "int chosen( int x );\n" + original
        )
        self.assertEqual(function(source, "chosen")[2], original)
        with self.assertRaisesRegex(ValueError, "found 0"):
            function("/* int absent(void) {} */", "absent")
        with self.assertRaisesRegex(ValueError, "found 2"):
            function(original + "\n" + original, "chosen")

    def test_nested_parameter_commas_and_comment_stars_do_not_add_arguments(self):
        source = (
            "void nested( int n /* not a pointer: *, (, ), ; {} */, "
            "void (*callback)(int, int), int values[3], "
            "double scalar /* , */, char *text ) /* ;{,} */\n{}"
        )
        self.assertEqual(len(function_parameters(source, "nested")), 5)
        self.assertEqual(null_arguments(source, "nested"),
                         ["0", "NULL", "NULL", "0", "NULL"])

    def test_line_comment_with_continuation_preserves_positions(self):
        source = "int f( int x, // ; }, ( comma ,\\\ncontinued comment }, ;\nint y )\n{}"
        masked = mask_c_comments(source)
        self.assertEqual(len(source), len(masked))
        self.assertNotIn("continued", masked)
        self.assertEqual(null_arguments(source, "f"), ["0", "0"])
        self.assertEqual(function(source, "f")[2], source)

    def test_void_and_empty_parameter_lists(self):
        self.assertEqual(null_arguments("void a(void) /* ;{,} */\n{}", "a"), [])
        self.assertEqual(null_arguments("void b() /* ;{,} */\n{}", "b"), [])


if __name__ == "__main__":
    unittest.main()

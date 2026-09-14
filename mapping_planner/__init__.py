"""
mapping_planner

Capa de DECISION (Python puro, sin escribir XML) para la familia
TERADATA_TO_SQL:

    Source metadata + Logical mappings
        |
    mapping_planner.planner.build_transformation_plan()
        |
    Transformation Plan
        |
    mapping_planner.translator.translate_plan_to_process_spec_fragment()
        |
    fragmento de ProcessSpec (transformations + mappings)
        |
    generator/spec_validator.py + generator/campanias_generator.py (SIN CAMBIOS)

El generador no sabe que este paquete existe: el fragmento que produce
translator.py se mezcla en un ProcessSpec ya existente (package/source/
destination declarados por el caller) y ese ProcessSpec completo pasa,
sin ninguna modificacion de codigo, por
generator.spec_validator.assert_valid_spec() y generator.campanias_generator.generate()
tal como hoy.

Scope congelado v1 (ver docs/mapping_planner_v1.md): tipos soportados
str/wstr/dbDate/numeric/i2/i4/i8; NO existe "destination schema" como
entidad de primera clase -- el Logical Mapping debe declarar toda la
metadata destino necesaria explicitamente; el planner NUNCA infiere
metadata faltante, solo clasifica y traduce lo ya declarado.
"""

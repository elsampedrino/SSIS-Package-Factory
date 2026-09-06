"""
project_context

Analizador de Project Context v1: lee (sin modificar) los archivos de
contexto de un proyecto SSIS existente -- *.dtproj, Project.params, *.conmgr
-- y los consolida en un unico dict "ProjectContext" (ver
docs/project_context.md).

Alcance de esta version (Modo A, solo analisis):
    NO crea ni modifica .dtproj / Project.params / .conmgr.
    NO descifra valores sensibles -- se tratan como opacos (nunca se expone
    el texto cifrado ni se lo confunde con un secreto en texto plano).
    NO se integra (todavia) con generator/campanias_generator.py.

Modulos:
    xml_helpers.py     -- namespaces y utilidades XML compartidas.
    params_parser.py   -- lee Project.params.
    conmgr_parser.py   -- lee un *.conmgr (dispatch por provider: TERADATA, OLEDB).
    dtproj_parser.py   -- lee un *.dtproj.
    context_builder.py -- consolida los tres en un ProjectContext.
    validator.py        -- validaciones sobre un ProjectContext ya construido.
"""

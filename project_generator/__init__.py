"""
project_generator

MODE B de generacion (project-generation-v1): ProjectSpec -> Project.params +
N x *.conmgr, para incorporar a un proyecto SSIS EXISTENTE. Espejo de
escritura de project_context/ (que solo lee) -- reutiliza sus mismos
formatos XML evidenciados, sin duplicar su logica de lectura ni crear una
representacion interna paralela de ProjectContext.

No genera ni modifica ningun .dtproj -- ver docs/project_generation_v1.md
para el scope congelado y las limitaciones documentadas (RetainSameConnection
OLEDB, integracion automatica en .dtproj).
"""

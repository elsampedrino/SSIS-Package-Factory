"""
generator

Generador MVP de paquetes SSIS, limitado exclusivamente al caso simple
Teradata Source -> Data Conversion -> OLE DB Destination (BipSuc_CampaniasVigentes
como referencia). Ver docs/generator_mvp.md para el diseño completo.

No soporta (todavia) Turnero ni ninguna de sus estructuras: Sequence
Containers, Execute SQL Task, transacciones, staging, Merge Join, Conditional
Split, Row Count, OLE DB Source.
"""

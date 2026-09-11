"""
profiles

Capa OPCIONAL de defaults corporativos para familias de generacion ya
estables. Resuelve un MinimalSpec (lo que realmente cambia por proyecto) a
los mismos ProjectSpec/ProcessSpec que hoy consumen, sin cambios,
project_generator.project_generator y generator.campanias_generator -- no
es una segunda implementacion de generacion XML, es una capa ANTERIOR que
completa defaults evidence-based antes de llamar a los generadores
existentes.

Los specs explicitos actuales (ProjectSpec/ProcessSpec completos, sin pasar
por ningun profile) siguen siendo validos y funcionan exactamente igual --
ver docs/teradata_to_sql_profile_v1.md.
"""

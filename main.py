"""
main.py

Ejemplo de uso de ssis_parser + ssis_validator: lee un .dtsx, genera su
representacion intermedia en JSON y corre la validacion estructural del IR
(por separado, sin modificar el resultado del parser).

Uso:
    python main.py [ruta_al_dtsx] [ruta_salida_json]

Si no se pasan argumentos, usa por defecto:
    Examples/Originals/BipSuc_CampaniasVigentes.dtsx -> analysis_output.json

La validacion se escribe junto al JSON de salida, con sufijo .validation.json
(ej. analysis_output.json -> analysis_output.validation.json).
"""

import sys

import ssis_parser
import ssis_validator


DEFAULT_INPUT = "Examples/Originals/BipSuc_CampaniasVigentes.dtsx"
DEFAULT_OUTPUT = "analysis_output.json"


def _validation_path(output_path: str) -> str:
    if output_path.endswith(".json"):
        return output_path[: -len(".json")] + ".validation.json"
    return output_path + ".validation.json"


def main() -> None:
    input_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT

    result = ssis_parser.parse_file(input_path)
    ssis_parser.to_json(result, output_path)

    validation = ssis_validator.validate_ir(result)
    validation_path = _validation_path(output_path)
    ssis_parser.to_json(validation, validation_path)

    n_flows = len(result["data_flows"])
    n_components = sum(len(df["components"]) for df in result["data_flows"])

    print(f"Archivo fuente: {result['source_file']}")
    print(f"Paquete (DTS:ObjectName): {result['package_name']}")
    print(f"Data flows encontrados: {n_flows}")
    print(f"Componentes encontrados: {n_components}")
    print(f"Salida escrita en: {output_path}")
    print(
        f"Validacion: valid={validation['valid']} "
        f"errors={len(validation['errors'])} warnings={len(validation['warnings'])}"
    )
    print(f"Validacion escrita en: {validation_path}")


if __name__ == "__main__":
    main()

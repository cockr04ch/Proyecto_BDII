from flask import Flask, jsonify, render_template, request
from neo4j import GraphDatabase, basic_auth

app = Flask(__name__)

# Es una buena práctica obtener la configuración de variables de entorno,
# pero por ahora lo dejaremos así.
URI = "bolt://localhost:7687"
AUTH = basic_auth("neo4j", "password")

def get_driver():
    return GraphDatabase.driver(URI, auth=AUTH)

def serialize_node(node):
    """Convierte un objeto Nodo de Neo4j a un diccionario serializable."""
    return {
        'id': node.element_id,
        'labels': list(node.labels),
        'properties': dict(node)
    }

@app.route("/")
def index():
    """Sirve la página principal."""
    return render_template("index.html")

@app.route("/nodos")
def obtener_nodos():
    """Obtiene y devuelve los nodos de la base de datos."""
    try:
        with get_driver().session() as session:
            resultado = session.run("MATCH (n) RETURN n LIMIT 25")
            # Usamos nuestra función para serializar cada nodo
            nodos = [serialize_node(registro["n"]) for registro in resultado]
            return jsonify(nodos)
    except Exception as e:
        # Devolvemos un error si algo falla (ej. no se puede conectar a la BD)
        return jsonify({"error": str(e)}), 500

def serialize_relationship(rel):
    """Convierte un objeto Relación de Neo4j a un diccionario serializable."""
    return {
        'id': rel.element_id,
        'type': rel.type,
        'properties': dict(rel)
    }

@app.route("/ruta-mas-corta")
def obtener_ruta_mas_corta():
    """Calcula y devuelve la ruta más corta entre dos nodos."""
    start_node_name = request.args.get("start_node")
    end_node_name = request.args.get("end_node")
    graph_name = "myGraph"

    if not start_node_name or not end_node_name:
        return jsonify({"error": "Faltan los parámetros 'start_node' o 'end_node'"}), 400

    try:
        with get_driver().session() as session:
            # Usamos una transacción para asegurar que las operaciones se ejecuten en orden.
            with session.begin_transaction() as tx:
                # 1. Borrar el grafo si existe para evitar errores.
                tx.run("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)

                # 2. Proyectar el grafo de nuevo con la configuración correcta.
                # Lo tratamos como no dirigido ya que las rutas son bidireccionales.
                tx.run("""
                CALL gds.graph.project(
                    $graph_name,
                    ['Zona', 'Distribuidor'],
                    {
                        CONECTA: {
                            properties: 'tiempo_minutos',
                            orientation: 'UNDIRECTED'
                        }
                    }
                )
                """, graph_name=graph_name)

                # 3. Ejecutar Dijkstra para encontrar la ruta más corta.
                query = """
                MATCH (start {nombre: $start_node}), (end {nombre: $end_node})
                WHERE (start:Zona OR start:Distribuidor) AND (end:Zona OR end:Distribuidor)
                
                CALL gds.shortestPath.dijkstra.stream($graph_name, {
                    sourceNode: id(start),
                    targetNode: id(end),
                    relationshipWeightProperty: 'tiempo_minutos'
                })
                YIELD totalCost, path
                
                RETURN
                    totalCost,
                    [node IN nodes(path) | node.nombre] AS node_names,
                    relationships(path) AS relationships
                LIMIT 1
                """
                result = tx.run(query, graph_name=graph_name, start_node=start_node_name, end_node=end_node_name)
                data = result.single()

            if not data:
                return jsonify({"error": "No se encontró una ruta."}), 404

            # Procesar el resultado para el frontend
            path_details = []
            node_names = data["node_names"]
            relationships = [serialize_relationship(rel) for rel in data["relationships"]]

            for i, rel in enumerate(relationships):
                path_details.append({
                    "start_node": node_names[i],
                    "end_node": node_names[i+1],
                    "relationship": rel
                })

            return jsonify({
                "total_cost": data["totalCost"],
                "path": path_details
            })

    except Exception as e:
        # Imprimir el error en la consola para depuración
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Ocurrió un error en el servidor al calcular la ruta.", "details": str(e)}), 500

if __name__ == "__main__":
    # El modo debug es útil para desarrollo
    app.run(debug=True, port=5001)


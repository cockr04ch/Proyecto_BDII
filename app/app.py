from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from neo4j import GraphDatabase, basic_auth

app = Flask(__name__)
CORS(app)

URI = "bolt://localhost:7687"
AUTH = basic_auth("neo4j", "password")

def get_driver():
    return GraphDatabase.driver(URI, auth=AUTH)

def serialize_node(node):
    return {
        'id': node.element_id,
        'labels': list(node.labels),
        'properties': dict(node)
    }

def serialize_relationship(rel):
    return {
        'id': rel.element_id,
        'type': rel.type,
        'properties': dict(rel)
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/nodos")
def obtener_nodos():
    try:
        with get_driver().session() as session:
            resultado = session.run("MATCH (n) RETURN n LIMIT 25")
            nodos = [serialize_node(registro["n"]) for registro in resultado]
            return jsonify(nodos)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ruta-mas-corta")
def obtener_ruta_mas_corta():
    start_node_name = request.args.get("start_node")
    end_node_name = request.args.get("end_node")
    graph_name = "myGraph"

    if not start_node_name or not end_node_name:
        return jsonify({"error": "Faltan los parametros 'start_node' o 'end_node'"}), 400

    try:
        with get_driver().session() as session:
            # Paso 1: Crear o actualizar peso_compuesto
            session.run("""
                MATCH ()-[r:CONECTA]->()
                SET r.trafico_actual_numerico = CASE r.trafico_actual
                    WHEN 'bajo' THEN 1.0
                    WHEN 'medio' THEN 2.0
                    WHEN 'alto' THEN 3.0
                    ELSE 1.0
                END,
                r.peso_compuesto = 
                    coalesce(r.tiempo_minutos, 0) * 0.6 + 
                    coalesce(r.trafico_actual_numerico, 0) * 0.3 + 
                    coalesce(r.trafico_numerico, 0) * 0.1
            """)

        with get_driver().session() as session:
            # Paso 2: Verificar si el grafo existe y eliminarlo si es necesario
            result = session.run("""
                CALL gds.graph.exists($graph_name) YIELD exists
                RETURN exists
            """, graph_name=graph_name)

            if result.single()["exists"]:
                session.run("""
                    CALL gds.graph.drop($graph_name) YIELD graphName
                    RETURN graphName
                """, graph_name=graph_name)

            # Paso 3: Proyectar el grafo con propiedad peso_compuesto
            session.run("""
                CALL gds.graph.project(
                    $graph_name,
                    {
                        Zona: { properties: [] },
                        Distribuidor: { properties: [] } 
                    },
                    {
                        CONECTA: {
                            type: 'CONECTA',
                            orientation: 'NATURAL',
                            properties: 'peso_compuesto'
                        }
                    }
                )
            """, graph_name=graph_name)

            # Paso 4: Obtener id(start) e id(end) para usar en Dijkstra
            start_result = session.run("MATCH (n {nombre: $nombre}) RETURN id(n) AS id", nombre=start_node_name)
            end_result = session.run("MATCH (n {nombre: $nombre}) RETURN id(n) AS id", nombre=end_node_name)

            start_data = start_result.single()
            end_data = end_result.single()

            if not start_data or not end_data:
                return jsonify({"error": "No se encontraron los nodos de origen o destino"}), 404

            start_id = start_data["id"]
            end_id = end_data["id"]

            # Ejecutar algoritmo de Dijkstra con el peso correcto
            result = session.run("""
                CALL gds.shortestPath.dijkstra.stream($graph_name, {
                    sourceNode: $start_id,
                    targetNode: $end_id,
                    relationshipWeightProperty: 'peso_compuesto'
                })
                YIELD totalCost, path

                RETURN
                    totalCost,
                    [node IN nodes(path) | node.nombre] AS node_names,
                    relationships(path) AS relationships
            """, graph_name=graph_name, start_id=start_id, end_id=end_id)

            data = result.single()

        if not data:
            return jsonify({"error": "No se encontro una ruta."}), 404

        node_names = data["node_names"]
        relationships = [serialize_relationship(rel) for rel in data["relationships"]]

        path_details = []
        for i, rel in enumerate(relationships):
            path_details.append({
                "start_node": node_names[i],
                "end_node": node_names[i + 1],
                "relationship": rel
            })

        return jsonify({
            "total_cost": data["totalCost"],
            "path": path_details
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Ocurrio un error en el servidor al calcular la ruta.",
            "details": str(e)
        }), 500

@app.route("/simulate", methods=['POST'])
def simulate_traffic():
    data = request.get_json()
    hour = data.get("hour")
    start_node_name = data.get("start_node")
    end_node_name = data.get("end_node")
    graph_name = "myGraph_simulated"

    if not all([hour, start_node_name, end_node_name]):
        return jsonify({"error": "Faltan los parametros 'hour', 'start_node' o 'end_node'"}), 400

    try:
        hour = int(hour)
    except (ValueError, TypeError):
        return jsonify({"error": "El parametro 'hour' debe ser un numero entero."}), 400


    # Definir el multiplicador basado en la hora
    if 7 <= hour < 10 or 17 <= hour < 20:
        time_multiplier = 1.8  # Hora pico
    elif 10 <= hour < 17:
        time_multiplier = 1.2  # Hora normal
    else:
        time_multiplier = 0.7  # Hora valle

    try:
        with get_driver().session() as session:
            # Paso 1: Crear o actualizar peso_compuesto_simulado
            session.run("""
                MATCH ()-[r:CONECTA]->()
                SET r.trafico_actual_numerico = CASE r.trafico_actual
                    WHEN 'bajo' THEN 1.0
                    WHEN 'medio' THEN 2.0
                    WHEN 'alto' THEN 3.0
                    ELSE 1.0
                END,
                r.peso_compuesto_simulado = 
                    (coalesce(r.tiempo_minutos, 0) * $time_multiplier) * 0.6 + 
                    coalesce(r.trafico_actual_numerico, 0) * 0.3 + 
                    coalesce(r.trafico_numerico, 0) * 0.1
            """, time_multiplier=time_multiplier)

            # El resto es similar a /ruta-mas-corta, pero con el nuevo peso

            # Paso 2: Verificar si el grafo existe y eliminarlo si es necesario
            result = session.run("CALL gds.graph.exists($graph_name) YIELD exists RETURN exists", graph_name=graph_name)
            if result.single()["exists"]:
                session.run("CALL gds.graph.drop($graph_name) YIELD graphName", graph_name=graph_name)

            # Paso 3: Proyectar el grafo con la propiedad de peso simulado
            session.run('''
                CALL gds.graph.project(
                    $graph_name,
                    {
                        Zona: { properties: [] },
                        Distribuidor: { properties: [] }
                    },
                    {
                        CONECTA: {
                            type: 'CONECTA',
                            orientation: 'NATURAL',
                            properties: 'peso_compuesto_simulado'
                        }
                    }
                )
            ''', graph_name=graph_name)

            # Paso 4: Obtener id(start) e id(end) para usar en Dijkstra
            start_result = session.run("MATCH (n {nombre: $nombre}) RETURN id(n) AS id", nombre=start_node_name)
            end_result = session.run("MATCH (n {nombre: $nombre}) RETURN id(n) AS id", nombre=end_node_name)

            start_data = start_result.single()
            end_data = end_result.single()

            if not start_data or not end_data:
                return jsonify({"error": "No se encontraron los nodos de origen o destino"}), 404

            start_id = start_data["id"]
            end_id = end_data["id"]

            # Ejecutar algoritmo de Dijkstra con el peso simulado
            result = session.run('''
                CALL gds.shortestPath.dijkstra.stream($graph_name, {
                    sourceNode: $start_id,
                    targetNode: $end_id,
                    relationshipWeightProperty: 'peso_compuesto_simulado'
                })
                YIELD totalCost, path
                RETURN
                    totalCost,
                    [node IN nodes(path) | node.nombre] AS node_names,
                    relationships(path) AS relationships
            ''', graph_name=graph_name, start_id=start_id, end_id=end_id)

            data = result.single()

            if not data:
                return jsonify({"error": "No se encontro una ruta simulada."}), 404

            node_names = data["node_names"]
            relationships = [serialize_relationship(rel) for rel in data["relationships"]]

            path_details = []
            for i, rel in enumerate(relationships):
                path_details.append({
                    "start_node": node_names[i],
                    "end_node": node_names[i + 1],
                    "relationship": rel
                })
            
            # Devolver el costo total y la ruta
            return jsonify({
                "total_cost": data["totalCost"],
                "path": path_details
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Ocurrio un error en el servidor al simular la ruta.",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(debug=True, port=5001)

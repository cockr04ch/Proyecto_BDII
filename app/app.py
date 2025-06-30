from flask import Flask, jsonify, render_template, request
from neo4j import GraphDatabase, basic_auth

app = Flask(__name__)

URI = "bolt://localhost:7687"
AUTH = basic_auth("neo4j", "Sonic/2002")

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
        return jsonify({"error": "Faltan los parámetros 'start_node' o 'end_node'"}), 400

    try:
        # 🛠️ Paso 1: Actualizar pesos
        with get_driver().session() as prep_session:
            prep_session.run("""
                MATCH ()-[r:CONECTA]->()
                SET r.trafico_actual_numerico = 
                    CASE r.trafico_actual
                        WHEN 'bajo' THEN 1.0
                        WHEN 'medio' THEN 2.0
                        WHEN 'alto' THEN 3.0
                        ELSE 1.0
                    END,
                    r.peso_compuesto = 
                        r.tiempo_minutos * 0.6 + 
                        r.trafico_actual_numerico * 0.3 + 
                        r.trafico_numerico * 0.1
            """)

        # ⚙️ Paso 2: Proyectar grafo y calcular ruta
        with get_driver().session() as session:
            with session.begin_transaction() as tx:
                tx.run("CALL gds.graph.drop($graph_name, false)", graph_name=graph_name)

                tx.run("""
                CALL gds.graph.project.cypher(
                    $graph_name,
                    'MATCH (n:Zona) RETURN id(n) AS id UNION MATCH (n:Distribuidor) RETURN id(n) AS id',
                    '
                    MATCH (a)-[r:CONECTA]->(b)
                    WHERE r.peso_compuesto IS NOT NULL
                    RETURN id(a) AS source, id(b) AS target, r.peso_compuesto AS weight
                    '
                )
                """, graph_name=graph_name)

                result = tx.run("""
                MATCH (start {nombre: $start_node}), (end {nombre: $end_node})
                WHERE (start:Zona OR start:Distribuidor) AND (end:Zona OR end:Distribuidor)

                CALL gds.shortestPath.dijkstra.stream($graph_name, {
                    sourceNode: id(start),
                    targetNode: id(end),
                    relationshipWeightProperty: 'weight'
                })
                YIELD totalCost, path

                RETURN
                    totalCost,
                    [node IN nodes(path) | node.nombre] AS node_names,
                    relationships(path) AS relationships
                LIMIT 5
                """, graph_name=graph_name, start_node=start_node_name, end_node=end_node_name)

                data = result.single()

        if not data:
            return jsonify({"error": "No se encontró una ruta."}), 404

        path_details = []
        node_names = data["node_names"]
        relationships = [serialize_relationship(rel) for rel in data["relationships"]]

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
            "error": "Ocurrió un error en el servidor al calcular la ruta.",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(debug=True, port=5001)

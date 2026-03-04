# Taxpy API MVP (Hostinger + Railway)

API minima para consumir el RAG desde web (PHP/HTML) sin mover la logica Python.

## Levantar servidor

```bash
python main.py api-server
```

Variables en `.env`:

- `API_SERVER_HOST` (default `0.0.0.0`)
- `API_SERVER_PORT` (default `8000`)
- `API_ACCESS_TOKEN` (opcional, recomendado en produccion)
- `API_DB_PATH` (sqlite local para cuota/uso)

## Endpoints

### `GET /health`

Verifica estado del servicio.

### `GET /usage/{user_id}`

Devuelve cuota mensual del usuario.

Headers (si `API_ACCESS_TOKEN` esta configurado):
- `Authorization: Bearer <TOKEN>` o
- `x-api-key: <TOKEN>`

### `POST /ask`

Body JSON:

```json
{
  "user_id": "abogado_demo_1",
  "question": "como tributan las acciones en chile",
  "mode": "tax",
  "top_juris": 6,
  "include_derogadas": false
}
```

- `mode: "tax"`: respuesta tributaria normal
- `mode: "writer"`: usa prompt editorial para redaccion tecnica

## Ejemplos `curl`

Sin token:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"demo1\",\"question\":\"articulo 17 n 8 lir\",\"mode\":\"tax\"}"
```

Con token:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TU_TOKEN" \
  -d "{\"user_id\":\"demo1\",\"question\":\"escribe un capitulo sobre art 17\",\"mode\":\"writer\"}"
```

## Integracion PHP simple

```php
<?php
$payload = [
  "user_id" => "cliente_123",
  "question" => "como tributan las acciones",
  "mode" => "tax",
  "top_juris" => 6,
  "include_derogadas" => false
];

$ch = curl_init("https://api.taxpy.cl/ask");
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
  "Content-Type: application/json",
  "Authorization: Bearer TU_TOKEN"
]);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
$response = curl_exec($ch);
curl_close($ch);
echo $response;
```

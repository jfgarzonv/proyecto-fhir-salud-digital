# setup_db.py - Crea la base de datos automaticamente si no existe
# Ejecutar ANTES del seed_db.py o del backend
# Uso: python setup_db.py

import os
import sys
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:1234@localhost:5432/fhir_salud_digital")

# Extraer componentes de la URL
# Formato: postgresql://usuario:password@host:puerto/nombre_db
try:
    parts = DATABASE_URL.replace("postgresql://", "").replace("postgres://", "")
    user_pass, host_db = parts.split("@")
    user, password = user_pass.split(":")
    host_port, db_name = host_db.split("/")
    if ":" in host_port:
        host, port = host_port.split(":")
    else:
        host = host_port
        port = "5432"
except Exception as e:
    print(f"Error parseando DATABASE_URL: {e}")
    print(f"  URL actual: {DATABASE_URL}")
    print(f"  Formato esperado: postgresql://usuario:password@host:puerto/nombre_db")
    sys.exit(1)

print(f"Configuracion detectada:")
print(f"  Host: {host}:{port}")
print(f"  Usuario: {user}")
print(f"  Base de datos: {db_name}")
print()

# Conectar a la base de datos 'postgres' (siempre existe) para crear la nueva
try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname="postgres"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    # Verificar si la base de datos ya existe
    cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (db_name,))
    exists = cursor.fetchone()

    if exists:
        print(f"La base de datos '{db_name}' ya existe. No se necesita crear.")
    else:
        cursor.execute(f'CREATE DATABASE "{db_name}"')
        print(f"Base de datos '{db_name}' creada exitosamente!")

    cursor.close()
    conn.close()

    # Ahora verificar conexion a la base de datos creada
    print(f"\nVerificando conexion a '{db_name}'...")
    conn2 = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=db_name
    )
    conn2.close()
    print("Conexion exitosa!")

    print(f"\nSiguiente paso: ejecutar 'python seed_db.py' para crear tablas y datos de prueba.")

except ImportError:
    print("Error: psycopg2 no esta instalado.")
    print("Ejecuta: pip install psycopg2-binary")
    sys.exit(1)
except psycopg2.OperationalError as e:
    error_msg = str(e)
    print(f"Error de conexion a PostgreSQL:")
    print(f"  {error_msg}")
    print()
    if "password authentication failed" in error_msg:
        print("SOLUCION: La contrasena en el .env no coincide con la de PostgreSQL.")
        print(f"  Contrasena actual en .env: {password}")
        print("  Cambiala en el archivo .env (DATABASE_URL)")
    elif "could not connect to server" in error_msg or "Connection refused" in error_msg:
        print("SOLUCION: PostgreSQL no esta corriendo o el host/puerto es incorrecto.")
        print("  1. Verificar que PostgreSQL este iniciado:")
        print("     - Windows: Buscar 'Servicios' > postgresql > Iniciar")
        print("     - Mac: brew services start postgresql")
        print(f"  2. Verificar que el puerto sea {port}")
    elif "role" in error_msg and "does not exist" in error_msg:
        print(f"SOLUCION: El usuario '{user}' no existe en PostgreSQL.")
        print("  Crear el usuario o cambiar el .env para usar 'postgres'")
    else:
        print("SOLUCION: Verificar los datos de conexion en el archivo .env")
    sys.exit(1)
except Exception as e:
    print(f"Error inesperado: {e}")
    sys.exit(1)

import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.security import generate_password_hash

load_dotenv()

app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    
    secret_key = "solo-desarrollo-cambia-esta-clave"
app.config["SECRET_KEY"] = secret_key

DB_USER = os.environ.get("POSTGRES_USER", "gimnasio_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("POSTGRES_DB", "gimnasio")


db_url = URL.create(
    drivername="postgresql+psycopg2",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME") or None
mail = Mail(app)
db = SQLAlchemy(app)

PLANES = {
    "Sala fitness": "25 €/mes",
    "Fuerza": "30 €/mes",
    "Completo": "40 €/mes",
}


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)


class Inscripcion(db.Model):
    __tablename__ = "inscripciones"

    id = db.Column(db.Integer, primary_key=True)
    email_usuario = db.Column(db.String(120), nullable=False, index=True)
    plan = db.Column(db.String(50), nullable=False)


def ahora_local():
    """Fecha/hora de España; si el contenedor no tiene zonas horarias, usa la hora del sistema."""
    try:
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except ZoneInfoNotFoundError:
        return datetime.now()


def enviar_correo(destinatario, asunto, mensaje):
    """Envía un correo si SMTP está configurado; los fallos no invalidan el registro."""
    if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        app.logger.warning("Correo NO enviado: faltan MAIL_USERNAME y/o MAIL_PASSWORD en el .env.")
        return False

    try:
        correo = Message(subject=asunto, recipients=[destinatario], body=mensaje)
        mail.send(correo)
        app.logger.info("Correo enviado a %s (%s).", destinatario, asunto)
        return True
    except Exception:
        app.logger.exception(
            "No se pudo enviar el correo. Revisa que MAIL_PASSWORD sea una contraseña de aplicación de Gmail."
        )
        return False


@app.cli.command("probar-correo")
@click.argument("destinatario")
def probar_correo(destinatario):
    """Envía un correo de prueba: flask --app app probar-correo tu@correo.com"""
    ok = enviar_correo(
        destinatario,
        "Prueba de correo - Gimnasio Fuerza",
        "Si estás leyendo esto, el envío de correos de la aplicación funciona.",
    )
    click.echo("Correo enviado correctamente." if ok else "No se pudo enviar. Mira los mensajes de error de arriba.")


@app.route("/")
def inicio():
    planes = [{"nombre": nombre, "precio": precio} for nombre, precio in PLANES.items()]
    return render_template("index.html", planes=planes)


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "GET":
        return render_template("registro.html")

    nombre = request.form.get("nombre", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not nombre or not email or not password:
        return render_template("resultado.html", mensaje="Debes rellenar todos los campos."), 400
    if len(nombre) > 60 or len(email) > 120:
        return render_template("resultado.html", mensaje="El nombre o el correo son demasiado largos."), 400
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return render_template("resultado.html", mensaje="Introduce un correo electrónico válido."), 400
    if len(password) < 8:
        return render_template("resultado.html", mensaje="La contraseña debe tener al menos 8 caracteres."), 400

    usuario = Usuario(
        nombre=nombre,
        email=email,
        password=generate_password_hash(password),
    )
    db.session.add(usuario)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("resultado.html", mensaje="Ese correo ya está registrado."), 409
    except OperationalError:
        db.session.rollback()
        app.logger.exception(
            "No se pudo conectar a PostgreSQL (¿contraseña de POSTGRES_PASSWORD distinta a la del volumen?)."
        )
        return render_template(
            "resultado.html",
            mensaje="No hay conexión con la base de datos. Avisa al administrador.",
        ), 503
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Error al guardar el usuario en la base de datos.")
        return render_template("resultado.html", mensaje="No se ha podido completar el registro. Inténtalo más tarde."), 500

    enviado = enviar_correo(
        email,
        "Bienvenido/a a Gimnasio Fuerza",
        f"Hola {nombre},\n\nTu registro en Gimnasio Fuerza se ha realizado correctamente.\n"
        "Ya puedes inscribirte en el plan que prefieras desde nuestra web.\n\n¡Nos vemos en el gimnasio!",
    )
    mensaje = f"Registro completado. Bienvenido/a, {nombre}."
    if enviado:
        mensaje += " Te hemos enviado un correo de confirmación."
    return render_template("resultado.html", mensaje=mensaje)


@app.route("/inscripcion", methods=["GET", "POST"])
def inscripcion():
    planes = [{"nombre": nombre, "precio": precio} for nombre, precio in PLANES.items()]
    if request.method == "GET":
        return render_template("index.html", planes=planes)

    email = request.form.get("email", "").strip().lower()
    plan = request.form.get("plan", "").strip()
    if not email or not plan:
        return render_template("resultado.html", mensaje="Indica tu correo y el plan."), 400
    if plan not in PLANES:
        return render_template("resultado.html", mensaje="El plan seleccionado no es válido."), 400

    usuario = db.session.execute(db.select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
    if usuario is None:
        return render_template("resultado.html", mensaje="Primero debes registrarte."), 404

    nueva_inscripcion = Inscripcion(email_usuario=email, plan=plan)
    db.session.add(nueva_inscripcion)
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        app.logger.exception("No se pudo conectar a PostgreSQL al guardar la inscripción.")
        return render_template(
            "resultado.html",
            mensaje="No hay conexión con la base de datos. Avisa al administrador.",
        ), 503
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Error al guardar la inscripción en la base de datos.")
        return render_template("resultado.html", mensaje="No se ha podido guardar la inscripción. Inténtalo más tarde."), 500

    numero_recibo = f"GF-{nueva_inscripcion.id:05d}"
    fecha = ahora_local().strftime("%d/%m/%Y %H:%M")
    cuerpo = (
        f"Hola {usuario.nombre},\n\n"
        "Gracias por tu compra en Gimnasio Fuerza. Este es tu recibo:\n\n"
        f"  Recibo nº: {numero_recibo}\n"
        f"  Fecha:     {fecha}\n"
        f"  Plan:      {plan}\n"
        f"  Importe:   {PLANES[plan]}\n\n"
        "Conserva este correo como justificante de tu compra.\n"
    )
    enviado = enviar_correo(email, f"Recibo {numero_recibo} - Gimnasio Fuerza", cuerpo)

    mensaje = f"Compra realizada: plan {plan} ({PLANES[plan]}). Recibo {numero_recibo}."
    if enviado:
        mensaje += " Te hemos enviado el recibo por correo."
    else:
        mensaje += " No se ha podido enviar el recibo por correo."
    return render_template("resultado.html", mensaje=mensaje)

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")

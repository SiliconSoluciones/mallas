from flask import Flask, render_template, redirect, url_for, request, flash, make_response, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_caching import Cache
from sqlalchemy import func
from datetime import datetime, timedelta
from collections import defaultdict
from io import StringIO
from urllib.parse import urlencode
from functools import wraps
from dotenv import load_dotenv
import csv
import os


app = Flask(__name__)
load_dotenv()


# Obtener la URI de la base de datos desde entorno o ponerla directamente
uri = os.environ.get('DATABASE_URL')

# Reemplazar el prefijo si es necesario
if uri.startswith('postgres://'):
    uri = uri.replace('postgres://', 'postgresql://', 1)

# Configuración de la base de datos
app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'clave_secreta_empresa_colombiana'

# Inicializar extensiones
db = SQLAlchemy(app)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})

def zip_lists(a, b):
    return zip(a, b)

def whatsapp_link(numero):
    # Eliminar espacios, guiones y paréntesis
    numero_limpio = ''.join(c for c in numero if c.isdigit())
    # Eliminar código de país si existe (asumiendo formato colombiano)
    if numero_limpio.startswith('57'):
        numero_limpio = numero_limpio[2:]
    return f"https://wa.me/57{numero_limpio}"

app.jinja_env.filters['whatsapp'] = whatsapp_link

app.jinja_env.filters['zip'] = zip_lists

# @app.before_first_request
def inicializar_base_datos():
    db.create_all()
    crear_usuarios_iniciales()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.es_admin:
            flash('Acceso restringido', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
    
@app.template_filter('number_format')
def number_format(value, decimal_places=2):
    try:
        return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return value
    
@app.template_filter('urlencode_skip_page')
def urlencode_skip_page_filter(args):
    filtered_args = args.copy()
    filtered_args.pop('page', None)
    return url_encode(filtered_args)


@app.before_request
def check_temporal_permissions():
    if current_user.is_authenticated and current_user.permiso_temporal:
        tiempo_transcurrido = (datetime.utcnow() - current_user.permiso_inicio).seconds
        if tiempo_transcurrido > current_user.permiso_duracion * 60:
            current_user.permiso_temporal = False
            db.session.commit()


login_manager = LoginManager(app)
login_manager.login_view = 'login'


# Modelos
class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    monto_total = db.Column(db.Float, nullable=True)
    abono = db.Column(db.Float, default=0)
    saldo = db.Column(db.Float)
    estado = db.Column(db.String(20), default='pendiente')
    asesora_id = db.Column(db.Integer, db.ForeignKey('asesora.id'))
    descripcion = db.Column(db.Text, nullable=True)
    etapa = db.Column(db.String(20), default='por_llamar')
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)  # Nueva fecha automática
    fecha_recordatorio = db.Column(db.Date, nullable=True)  # Nueva fecha editable


class Asesora(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(50), unique=True, nullable=False)
    contraseña = db.Column(db.String(200), nullable=False)
    es_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='asesora')
    clientes = db.relationship('Cliente', backref='asesora', lazy=True)
    activa = db.Column(db.Boolean, default=True)  # <- Campo agregado
    permiso_temporal = db.Column(db.Boolean, default=False)
    permiso_inicio = db.Column(db.DateTime)
    permiso_duracion = db.Column(db.Integer)

@login_manager.user_loader
def load_user(user_id):
    return Asesora.query.get(int(user_id))

def validar_fecha(fecha_str):
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d')
        if not (2000 <= fecha.year <= 2100):
            raise ValueError
        return fecha.date()
    except:
        return None


def escapejs_filter(value):
    """Filtro personalizado para escapar strings en JavaScript"""
    escape_chars = {
        '\\': '\\u005C',
        '\'': '\\u0027',
        '"': '\\u0022',
        '>': '\\u003E',
        '<': '\\u003C',
        '&': '\\u0026',
        '=': '\\u003D',
        '-': '\\u002D',
        ';': '\\u003B',
        '\u2028': '\\u2028',
        '\u2029': '\\u2029'
    }
    for char, escaped in escape_chars.items():
        value = value.replace(char, escaped)
    return value

app.jinja_env.filters['escapejs'] = escapejs_filter

def temporal_permission_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.permiso_temporal:
            if current_user.es_admin:
                return f(*args, **kwargs)
            flash('Acceso restringido', 'danger')
            return redirect(url_for('dashboard'))
        
        tiempo_transcurrido = (datetime.utcnow() - current_user.permiso_inicio).seconds
        if tiempo_transcurrido > current_user.permiso_duracion * 60:
            current_user.permiso_temporal = False
            db.session.commit()
            flash('Permiso temporal expirado', 'warning')
            return redirect(url_for('dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function


# Rutas

@app.route('/actualizar_descripcion/<int:id>', methods=['POST'])
@login_required
def actualizar_descripcion(id):
    cliente = Cliente.query.get_or_404(id)
    
    try:
        # Verificar permiso básico
        if cliente.asesora_id != current_user.id and not current_user.es_admin:
            abort(403)

        # Actualizar descripción
        cliente.descripcion = request.form.get('descripcion', '')
        db.session.commit()
        flash("Descripción actualizada", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/editar_cliente_limited/<int:id>', methods=['POST'])
@login_required
@temporal_permission_required
def editar_cliente_limited(id):
    cliente = Cliente.query.get_or_404(id)
    
    try:
        # Verificar permiso sobre el cliente
        if cliente.asesora_id != current_user.id and not current_user.es_admin:
            abort(403)

        # Validar monto total
        nuevo_monto = float(request.form.get('monto_total'))
        if nuevo_monto < cliente.abono:
            raise ValueError("El monto total no puede ser menor al abono")

        # Actualizar campos
        cliente.nombre = request.form.get('nombre')
        cliente.telefono = request.form.get('telefono')
        cliente.descripcion = request.form.get('descripcion', '')
        cliente.monto_total = nuevo_monto
        cliente.saldo = nuevo_monto - cliente.abono
        
        db.session.commit()
        flash("Cambios guardados correctamente", "success")
        
    except ValueError as e:
        db.session.rollback()
        flash(f"Error de validación: {str(e)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/activar_permisos/<int:id>', methods=['POST'])
@admin_required
def activar_permisos(id):
    asesora = Asesora.query.get_or_404(id)
    try:
        asesora.permiso_temporal = True
        asesora.permiso_inicio = datetime.utcnow()
        asesora.permiso_duracion = 10  # 10 minutos por defecto
        db.session.commit()
        flash("Permisos temporales activados por 10 minutos", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for('admin_asesoras'))

@app.route('/actualizar_fecha_recordatorio/<int:id>', methods=['POST'])
@login_required
def actualizar_fecha_recordatorio(id):
    cliente = Cliente.query.get_or_404(id)
    
    if not current_user.es_admin and cliente.asesora_id != current_user.id:
        abort(403)
    
    try:
        nueva_fecha = validar_fecha(request.form.get('fecha_recordatorio'))
        cliente.fecha_recordatorio = nueva_fecha
        db.session.commit()
        flash("Fecha de recordatorio actualizada", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/eliminar_cliente/<int:id>', methods=['POST'])
@login_required
def eliminar_cliente(id):
    if not current_user.es_admin:
        abort(403)
    
    cliente = Cliente.query.get_or_404(id)
    try:
        db.session.delete(cliente)
        db.session.commit()
        flash("Cliente eliminado exitosamente", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/exportar_ventas')
@login_required
def exportar_ventas():
    if not current_user.es_admin:
        flash("Acceso restringido", "danger")
        return redirect(url_for('ventas_finalizadas'))
    
    # Obtener datos
    clientes = Cliente.query.filter_by(estado='finalizado').all()
    
    # Crear CSV
    csv_buffer = StringIO()
    writer = csv.writer(csv_buffer)
    
    # Cabeceras
    writer.writerow([
        'Cliente', 'Teléfono', 'Fecha', 
        'Asesora', 'Monto Total'
    ])
    
    # Datos
    for cliente in clientes:
        writer.writerow([
            cliente.nombre,
            cliente.telefono,
            cliente.fecha_recordatorio.strftime('%d/%m/%Y'),
            cliente.asesora.nombre if cliente.asesora else '',
            cliente.monto_total
        ])
    
    # Configurar respuesta
    response = make_response(csv_buffer.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=ventas_finalizadas.csv'
    response.headers['Content-type'] = 'text/csv'
    
    return response

@app.route('/actualizar_abono/<int:id>', methods=['POST'])
@login_required
def actualizar_abono(id):
    cliente = Cliente.query.get_or_404(id)
    
    # Verificar permisos primero
    if not current_user.es_admin and cliente.asesora_id != current_user.id:
        abort(403)

    try:
        nuevo_abono = float(request.form.get('abono', 0))
        
        # Validación de monto total
        if cliente.monto_total <= 0:
            flash("Debe establecer un monto total primero", "danger")
            return redirect(url_for('clientes'))
            
        # Validación de límite de abono
        if nuevo_abono > cliente.monto_total:
            flash("El abono no puede exceder el monto total", "danger")
            return redirect(url_for('clientes'))
            
        # Actualizar valores
        cliente.abono = nuevo_abono
        cliente.saldo = cliente.monto_total - nuevo_abono
        
        db.session.commit()
        flash("Abono actualizado exitosamente", "success")
        
    except ValueError:
        flash("El valor ingresado no es válido", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error inesperado: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/')
@login_required
def index():
    asesoras = []
    if current_user.es_admin:
        asesoras = Asesora.query.all()
    return render_template('index.html', asesoras=asesoras)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contraseña = request.form.get('contraseña')
        asesora = Asesora.query.filter_by(usuario=usuario).first()

        if asesora and check_password_hash(asesora.contraseña, contraseña):
            login_user(asesora)
            flash(f'¡Bienvenido(a) {asesora.nombre}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas', 'danger')
    
    return render_template('auth/login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada correctamente', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    hoy = datetime.now()
    mes_actual = hoy.strftime('%Y-%m') + '-01'  # Formato: '2025-05-01'
    
    # Ventas del mes actual
    ventas_mes_actual = db.session.query(func.sum(Cliente.monto_total)).filter(
    func.date_trunc('month', Cliente.fecha_creacion) == mes_actual,
    Cliente.estado == 'finalizado'
    ).scalar() or 0.0

    # Ventas del mes anterior (para cálculo de porcentaje)
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).strftime('%Y-%m') + '-01'
    ventas_mes_anterior = db.session.query(func.sum(Cliente.monto_total)).filter(
    func.date_trunc('month', Cliente.fecha_creacion) == mes_anterior,
    Cliente.estado == 'finalizado'
    ).scalar() or 0.0

    # Cálculo de porcentaje de cambio
    try:
        porcentaje_cambio = round(
            ((ventas_mes_actual - ventas_mes_anterior) / ventas_mes_anterior) * 100, 
            1
        ) if ventas_mes_anterior != 0 else 100.0
    except:
        porcentaje_cambio = 0.0

    # Clientes pendientes
    if current_user.es_admin:
        clientes_pendientes = Cliente.query.filter_by(estado='pendiente').count()
    else:
        clientes_pendientes = Cliente.query.filter_by(
            asesora_id=current_user.id,
            estado='pendiente'
        ).count()

    # Datos de asesoras
    total_asesoras = Asesora.query.count()
    asesoras_activas = Asesora.query.filter_by(activa=True).count()

    return render_template('dashboard.html',
        ventas_mes_actual=ventas_mes_actual,
        porcentaje_cambio=porcentaje_cambio,
        clientes_pendientes=clientes_pendientes,
        total_asesoras=total_asesoras,
        asesoras_activas=asesoras_activas
    )

@app.route('/clientes')
@login_required
def clientes():
    query = Cliente.query.filter_by(estado='pendiente')
    if not current_user.es_admin:
        query = query.filter_by(asesora_id=current_user.id)
    return render_template('clientes.html', clientes=query.all())

    asesoras = Asesora.query.all()  # Obtener todas las asesoras


@app.route('/registrar-cliente')
@login_required
def registrar_cliente():
    asesoras = []
    if current_user.es_admin:
        asesoras = Asesora.query.all()
    return render_template('index.html', asesoras=asesoras)

@app.route('/finalizar_venta/<int:id>', methods=['POST'])
@login_required
def finalizar_venta(id):
    cliente = Cliente.query.get_or_404(id)
    
    try:
        if cliente.monto_total <= 0:
            flash("Debe registrar un monto total antes de finalizar", "danger")
            return redirect(url_for('clientes'))

        cliente.estado = 'finalizado'
        db.session.commit()
        flash("Venta finalizada exitosamente", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/ventas-finalizadas')
@login_required
def ventas_finalizadas():
    if current_user.permiso_temporal:
        flash("Acceso restringido durante permisos temporales", "danger")
        return redirect(url_for('dashboard'))
    # Parámetros de paginación y filtros
    page = request.args.get('page', 1, type=int)
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')
    monto_min = request.args.get('monto_min', type=float)
    asesora_id = request.args.get('asesora', type=int)
    search_query = request.args.get('search', '').strip()

    # Construcción de la consulta base
    query = Cliente.query.filter(Cliente.estado == 'finalizado')

    # Aplicar filtros si vienen en la URL
    if fecha_inicio:
        try:
            fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
            query = query.filter(Cliente.fecha_creacion >= fecha_inicio_dt)
        except ValueError:
            flash("Formato de fecha de inicio inválido (Use YYYY-MM-DD)", "warning")

    if fecha_fin:
        try:
            fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
            query = query.filter(Cliente.fecha_creacion <= fecha_fin_dt)
        except ValueError:
            flash("Formato de fecha final inválido (Use YYYY-MM-DD)", "warning")

    if monto_min:
        query = query.filter(Cliente.monto_total >= monto_min)

    if asesora_id:
        query = query.filter(Cliente.asesora_id == asesora_id)

    # Filtro de búsqueda mejorado
    if search_query:
        search = f"%{search_query}%"
        query = query.filter(
            db.or_(
                Cliente.nombre.ilike(search),
                Cliente.telefono.ilike(search),
                Cliente.descripcion.ilike(search)
            )
        )

    # Paginación
    pagination = query.order_by(Cliente.fecha_creacion.desc()).paginate(
        page=page, 
        per_page=10, 
        error_out=False
    )
    clientes = pagination.items

    # Traer todas las asesoras para el select
    asesoras = Asesora.query.order_by(Asesora.nombre).all()

    return render_template(
        'ventas_finalizadas.html',
        clientes=clientes,
        pagination=pagination,
        asesoras=asesoras,
        selected_asesora=asesora_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        monto_min=monto_min,
        search_query=search_query
    )

@app.route('/detalle_ventas')
@login_required
@admin_required
@cache.cached(timeout=300, key_prefix=lambda: f"ventas_{request.args.get('fecha_inicio','')}_{request.args.get('fecha_fin','')}")
def detalle_ventas():
    if not current_user.es_admin:
        flash("Acceso restringido a administradores", "danger")
        return redirect(url_for('dashboard'))
    

    # Manejo de fechas
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')
    


    # Consulta base mejorada
    query = db.session.query(
    Asesora.nombre,
    func.to_char(Cliente.fecha_creacion, 'YYYY').label('año'),
    func.to_char(Cliente.fecha_creacion, 'MM').label('mes'),
    func.to_char(Cliente.fecha_creacion, 'DD').label('dia'),
    func.sum(Cliente.monto_total).label('total_ventas')
    ).join(Cliente, Asesora.id == Cliente.asesora_id)

    # Aplicar filtros de fechas
    if fecha_inicio:
        query = query.filter(Cliente.fecha_creacion >= fecha_inicio)
    if fecha_fin:
        query = query.filter(Cliente.fecha_creacion <= fecha_fin)

    # Ejecutar consulta con agrupación y ordenamiento
    resultados = query.group_by(
    Asesora.nombre,
    func.date_trunc('day', Cliente.fecha_creacion)  # Agrupa por día completo
    ).order_by(
    func.date_trunc('day', Cliente.fecha_creacion).desc(),
    Asesora.nombre.asc()
    ).all()

    # Diccionario para nombres de meses en español
    meses_es = {
        '01': 'Enero', '02': 'Febrero', '03': 'Marzo',
        '04': 'Abril', '05': 'Mayo', '06': 'Junio',
        '07': 'Julio', '08': 'Agosto', '09': 'Septiembre',
        '10': 'Octubre', '11': 'Noviembre', '12': 'Diciembre'
    }

 # Estructura de datos organizada
    ventas_organizadas = {}
    for row in resultados:
        año = row.año
        mes = row.mes
        dia = int(row.dia)
        clave_mes = f"{año}-{mes}"
        
        # Construir estructura jerárquica
        if año not in ventas_organizadas:
            ventas_organizadas[año] = {}
            
        if clave_mes not in ventas_organizadas[año]:
            ventas_organizadas[año][clave_mes] = {
                'mes_nombre': meses_es[mes],
                'dias': {},
                'asesoras_mes': defaultdict(float),
                'total_mes': 0.0
            }

        # Obtener referencia al mes actual
        mes_actual = ventas_organizadas[año][clave_mes]
        
        # Actualizar totales del mes
        mes_actual['asesoras_mes'][row.nombre] += float(row.total_ventas)
        mes_actual['total_mes'] += float(row.total_ventas)
        
        # Manejar la estructura por días
        if dia not in mes_actual['dias']:
            mes_actual['dias'][dia] = {
                'asesoras': [],
                'totales': []
            }
            
        mes_actual['dias'][dia]['asesoras'].append(row.nombre)
        mes_actual['dias'][dia]['totales'].append(float(row.total_ventas or 0.0))

    return render_template(
        'detalle_ventas.html',
        ventas=ventas_organizadas,
        fecha_inicio=fecha_inicio or '',
        fecha_fin=fecha_fin or '',
        now=datetime.now()  
    )


@app.route('/actualizar_etapa/<int:id>', methods=['POST'])
@login_required
def actualizar_etapa(id):
    cliente = Cliente.query.get_or_404(id)
    
    # Verificar permisos
    if not current_user.es_admin and cliente.asesora_id != current_user.id:
        abort(403) # type: ignore
    
    nueva_etapa = request.form.get('etapa')
    
    if nueva_etapa in ['por_llamar', 'fabricacion', 'por_entregar']:
        try:
            cliente.etapa = nueva_etapa
            db.session.commit()
            flash("Etapa actualizada correctamente", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

@app.route('/reabrir_venta/<int:id>', methods=['POST'])
@login_required
def reabrir_venta(id):
    cliente = Cliente.query.get_or_404(id)
    try:
        cliente.estado = 'pendiente'
        db.session.commit()
        flash("Venta reabierta exitosamente", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for('ventas_finalizadas'))

@app.route('/editar_cliente/<int:id>', methods=['POST'])
@login_required
@temporal_permission_required
def editar_cliente(id):
    # Verificar si es administrador
    if not current_user.es_admin:
        abort(403, description="Acceso restringido a administradores") # type: ignore

    cliente = Cliente.query.get_or_404(id)
    
    try:
        # Validación de campos
        nuevo_nombre = request.form.get('nombre', '').strip()
        if not nuevo_nombre:
            raise ValueError("El nombre no puede estar vacío")
            
        nuevo_telefono = request.form.get('telefono', '').strip()
        if len(nuevo_telefono) != 10 or not nuevo_telefono.isdigit():
            raise ValueError("Teléfono debe tener 10 dígitos numéricos")
            
        nueva_fecha = validar_fecha(request.form.get('fecha_creacion'))
        if request.form.get('fecha_creacion') and not nueva_fecha:
            raise ValueError("Formato de fecha inválido (Use YYYY-MM-DD)")
            
        nuevo_monto = request.form.get('monto_total', '0').strip()
        try:
            monto = float(nuevo_monto)
            if monto <= 0:
                raise ValueError("El monto debe ser mayor a cero")
        except ValueError:
            raise ValueError("Monto debe ser un número válido")

        # Actualizar campos
        cliente.nombre = nuevo_nombre
        cliente.telefono = nuevo_telefono
        cliente.fecha_recordatorio= nueva_fecha
        cliente.monto_total = monto
        cliente.descripcion = request.form.get('descripcion', '').strip()
        
        # Si el modelo tiene relación con Asesora
        if 'asesora_id' in request.form:
            cliente.asesora_id = request.form.get('asesora_id') or None

        db.session.commit()
        flash("Cliente actualizado exitosamente", "success")
        
    except ValueError as ve:
        db.session.rollback()
        flash(f"Error de validación: {str(ve)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error crítico al actualizar: {str(e)}", "danger")
    
    return redirect(url_for('clientes'))

def validar_fecha(fecha_str):
    try:
        return datetime.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else None
    except (ValueError, TypeError):
        return None

@app.route('/cliente/<int:id>')
@login_required
def detalle_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    return render_template('detalle_cliente.html', cliente=cliente)

@app.route('/agregar_cliente', methods=['POST'])
@login_required
def agregar_cliente():
    try:
        if current_user.es_admin:
            asesora_id = int(request.form.get('asesora_id'))
        else:
            asesora_id = current_user.id

        fecha_recordatorio=validar_fecha(request.form.get('fecha_recordatorio'))

        

        nuevo_cliente = Cliente(
            nombre=request.form.get('nombre'),
            telefono=request.form.get('telefono'),
            fecha_recordatorio=fecha_recordatorio,
            asesora_id=asesora_id,
            monto_total=float(request.form.get('monto')or 0),
            abono=float(request.form.get('abono') or 0),
            descripcion = request.form.get('descripcion', ''),
            
        )
        nuevo_cliente.saldo = nuevo_cliente.monto_total - nuevo_cliente.abono
        db.session.add(nuevo_cliente)
        db.session.commit()
        flash('Cliente registrado exitosamente', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('clientes'))

# Admin routes
@app.route('/admin/asesoras')
@login_required
def admin_asesoras():
    if not current_user.es_admin:
        flash('Acceso restringido', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('admin/asesoras.html', asesoras=Asesora.query.all())

@app.route('/admin/asesoras/nueva', methods=['GET', 'POST'])
@login_required
def nueva_asesora():
    if not current_user.es_admin:
        flash("Acceso restringido", "danger")
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            nueva_asesora = Asesora(
                nombre=request.form.get('nombre'),
                usuario=request.form.get('usuario'),
                contraseña=generate_password_hash(request.form.get('contraseña')),
                es_admin='es_admin' in request.form
            )
            db.session.add(nueva_asesora)
            db.session.commit()
            flash("Asesora creada exitosamente", "success")
            return redirect(url_for('admin_asesoras'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    
    return render_template('admin/nueva_asesora.html')

@app.route('/admin/asesoras/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_asesora(id):
    if not current_user.es_admin:
        flash("Acceso restringido", "danger")
        return redirect(url_for('dashboard'))
    
    asesora = Asesora.query.get_or_404(id)
    try:
        # En lugar de eliminar, marcamos como inactiva
        asesora.activa = False
        db.session.commit()
        flash("Asesora marcada como inactiva", "warning")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    
    return redirect(url_for('admin_asesoras'))

@app.route('/admin/asesora/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_asesora(id):
    if not current_user.es_admin:
        flash("Acceso no autorizado", "danger")
        return redirect(url_for('dashboard'))
    
    asesora = Asesora.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            # Validar usuario único
            nuevo_usuario = request.form.get('usuario')
            if Asesora.query.filter(Asesora.usuario == nuevo_usuario, Asesora.id != id).first():
                flash("¡El usuario ya existe!", "danger")
                return redirect(url_for('editar_asesora', id=id))
            
            # Actualizar datos
            asesora.nombre = request.form.get('nombre')
            asesora.usuario = nuevo_usuario
            asesora.activa = 'activa' in request.form  # Nueva línea
            asesora.es_admin = 'es_admin' in request.form
            
            if request.form.get('contraseña'):
                asesora.contraseña = generate_password_hash(request.form.get('contraseña'))
            
            db.session.commit()
            flash("Cambios guardados exitosamente", "success")
            return redirect(url_for('admin_asesoras'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    
    return render_template('admin/editar_asesora.html', asesora=asesora)

def crear_usuarios_iniciales():
    with app.app_context():
        db.create_all()
        
        if not Asesora.query.filter_by(usuario='Mallas').first():
            admin = Asesora(
                nombre="Hiper Mallas",
                usuario="Mallas",
                contraseña=generate_password_hash("mallas2020"),
                es_admin=True
            )
            db.session.add(admin)
        
        asesoras_base = [
            {"nombre": "Asesora 1", "usuario": "asesora1", "contraseña": "clave1"},
            {"nombre": "Asesora 2", "usuario": "asesora2", "contraseña": "clave2"},
            {"nombre": "Asesora 3", "usuario": "asesora3", "contraseña": "clave3"},
            {"nombre": "Asesora 4", "usuario": "asesora4", "contraseña": "clave4"}
        ]
        
        for a in asesoras_base:
            if not Asesora.query.filter_by(usuario=a['usuario']).first():
                db.session.add(Asesora(
                    nombre=a['nombre'],
                    usuario=a['usuario'],
                    contraseña=generate_password_hash(a['contraseña']),
                    es_admin=False
                ))
        
        db.session.commit()

@app.cli.command("init_db")
def init_db():
    with app.app_context():
        db.create_all()
        crear_usuarios_iniciales()
        print("Base de datos inicializada")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

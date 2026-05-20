# ============================================================
# SISTEMA DE CITAS MÉDICAS - PARTE 3
# Planificación de Procesos, Concurrencia y Sincronización
# ============================================================
# Para ejecutar:
#   pip install psutil
#   python parte3_citas_medicas.py
# ============================================================


# ============================================================
# BLOQUE 1: IMPORTACIONES Y CONFIGURACIÓN GLOBAL
# ============================================================
# Este bloque define todas las librerías necesarias y las
# constantes del sistema. Usamos:
#   - threading: para hilos reales del SO (concurrencia)
#   - multiprocessing: para procesos reales (de la Parte 2)
#   - psutil: para monitoreo REAL de CPU, RAM y procesos
#   - queue / semaphore / lock: para sincronización
# ============================================================

import os
import time
import random
import threading
import multiprocessing
from multiprocessing import Value, Lock as MpLock
import psutil
import sqlite3
from datetime import datetime
from collections import deque

# --- Constantes del planificador ---
MAX_WORKERS         = 4      # Máximo de citas procesadas en paralelo (semáforo)
SCHEDULER_QUANTUM   = 2      # Quantum de tiempo (seg) para Round-Robin
MAX_RAM_MB          = 1000   # Capacidad RAM simulada (de la Parte 2)
MAX_RETRIES         = 3      # Reintentos antes de rechazar una cita
LOG_FILE            = "parte3_run.log"

# --- Estados de proceso (Simulación Académica) ---
# Estos estados modelan el ciclo de vida de un proceso en un SO real:
#   NEW       -> el proceso fue creado pero aún no está listo
#   READY     -> está en la cola de listos, esperando la CPU
#   RUNNING   -> está usando la CPU ahora mismo
#   WAITING   -> bloqueado esperando un recurso (RAM, I/O, etc.)
#   TERMINATED-> terminó su ejecución (éxito o rechazo)
STATES = ["NEW", "READY", "RUNNING", "WAITING", "TERMINATED"]

# Variables compartidas entre hilos (estado global del sistema)
shared_stats = {
    "total_processed": 0,
    "total_rejected":  0,
    "total_waiting":   0,
    "ram_current":     0,
    "ram_peak":        0,
}

# Semáforo: controla que como máximo MAX_WORKERS citas se
# procesen al mismo tiempo. Los demás quedan en WAITING.
worker_semaphore = threading.Semaphore(MAX_WORKERS)

# Mutex global para variables compartidas entre hilos
stats_lock = threading.Lock()


# ============================================================
# BLOQUE 2: SIMULACIÓN ACADÉMICA DE ESTADOS Y PLANIFICADOR
# ============================================================
# Aquí se modela el ciclo de vida de un proceso siguiendo la
# teoría de Sistemas Operativos:
#
#   NEW -> READY -> RUNNING -> WAITING -> RUNNING -> TERMINATED
#
# El planificador implementa Round-Robin con quantum fijo:
#   - Cada "cita" recibe la CPU por SCHEDULER_QUANTUM segundos.
#   - Si no termina, vuelve a la cola de READY (preemption).
#   - Esto evita la inanición (starvation) de citas largas.
#
# SECCIÓN CRÍTICA: La transición de estado y el acceso al
# contador de procesos activos deben protegerse con un mutex
# para evitar condiciones de carrera (race conditions).
# ============================================================

class AppointmentProcess:
    """
    Representa una cita médica como un 'proceso' del SO.
    Mantiene su propio estado, prioridad y métricas de tiempo.
    """

    def __init__(self, pid, appointment_id, patient_name, doctor, priority=1):
        self.pid             = pid
        self.appointment_id  = appointment_id
        self.patient_name    = patient_name
        self.doctor          = doctor
        self.priority        = priority          # 1=alta, 2=media, 3=baja
        self.state           = "NEW"
        self.arrival_time    = time.time()
        self.start_time      = None
        self.end_time        = None
        self.cpu_bursts      = random.randint(1, 4)  # Cuántos quantums necesita
        self.bursts_done     = 0
        self.required_ram    = random.randint(150, 400)

    def transition(self, new_state, log_fn):
        """
        Realiza la transición de estado con validación.
        Solo se permiten transiciones válidas según la máquina de estados.
        """
        valid_transitions = {
            "NEW":        ["READY"],
            "READY":      ["RUNNING"],
            "RUNNING":    ["WAITING", "TERMINATED"],
            "WAITING":    ["READY"],
            "TERMINATED": []
        }
        if new_state in valid_transitions[self.state]:
            old = self.state
            self.state = new_state
            log_fn(f"[PID {self.pid}] {old} -> {new_state} | {self.patient_name} / {self.doctor}")
        else:
            log_fn(f"[PID {self.pid}] TRANSICIÓN INVÁLIDA: {self.state} -> {new_state} (ignorada)")

    def turnaround_time(self):
        """Tiempo total desde que llegó hasta que terminó."""
        if self.end_time:
            return round(self.end_time - self.arrival_time, 2)
        return None

    def waiting_time(self):
        """Tiempo que estuvo en cola sin usar CPU."""
        if self.start_time:
            return round(self.start_time - self.arrival_time, 2)
        return None


class RoundRobinScheduler:
    """
    Planificador Round-Robin con quantum fijo.
    Mantiene una cola de READY y despacha procesos en orden FIFO,
    preemptando si el proceso no terminó en su quantum.
    """

    def __init__(self, quantum=SCHEDULER_QUANTUM):
        self.quantum     = quantum
        self.ready_queue = deque()
        self.lock        = threading.Lock()

    def enqueue(self, proc):
        """Agrega un proceso a la cola de READY (sección crítica)."""
        with self.lock:
            proc.state = "READY"
            self.ready_queue.append(proc)

    def dequeue(self):
        """Saca el siguiente proceso de la cola (sección crítica)."""
        with self.lock:
            if self.ready_queue:
                return self.ready_queue.popleft()
            return None

    def requeue(self, proc):
        """
        Devuelve un proceso a la cola después de que su quantum expiró.
        Esto es la 'preemption' del Round-Robin.
        """
        with self.lock:
            proc.state = "READY"
            self.ready_queue.append(proc)

    def has_work(self):
        with self.lock:
            return len(self.ready_queue) > 0


# ============================================================
# BLOQUE 3: PROCESOS REALES DEL SO Y CONCURRENCIA CON HILOS
# ============================================================
# Aquí se usan hilos REALES del sistema operativo (threading).
# Cada hilo procesa una cita independientemente.
#
# DIFERENCIA PROCESO vs HILO:
#   - Proceso (multiprocessing): memoria separada, mayor aislamiento,
#     más costoso. Usado en la Parte 2 para el control de admisión.
#   - Hilo (threading): comparte memoria con el proceso padre,
#     más ligero. Ideal para I/O concurrente (BD, notificaciones).
#
# CONCURRENCIA OBSERVABLE:
#   - El semáforo MAX_WORKERS limita cuántos hilos corren a la vez.
#   - Se puede ver en la consola que varios hilos se ejecutan
#     simultáneamente (timestamps solapados en el log).
# ============================================================

def log_event_thread(message):
    """
    Función de logging thread-safe usando llamadas directas al SO.
    Usa os.open/os.write/os.close para acceso directo (como Parte 2).
    """
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    thread_id = threading.current_thread().name
    log_line  = f"[{timestamp}][{thread_id}] {message}\n"

    # Escritura directa al SO (sin buffering de Python)
    fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.write(fd, log_line.encode("utf-8"))
    os.close(fd)

    print(log_line, end="")


def worker_thread(proc, scheduler, ram_value, ram_lock):
    """
    Hilo real del SO que ejecuta el ciclo de vida de una cita.

    Flujo:
      1. Adquiere el semáforo (o espera si ya hay MAX_WORKERS activos)
      2. Intenta reservar RAM (mutex sobre ram_value)
      3. Ejecuta quantums de CPU con el planificador Round-Robin
      4. Libera RAM y semáforo al terminar
    """
    log_event_thread(f"[PID {proc.pid}] Hilo iniciado para {proc.patient_name}")

    # Listar información del proceso real del SO
    current_proc = psutil.Process(os.getpid())
    log_event_thread(
        f"[PID {proc.pid}] Proceso SO real: PID={current_proc.pid}, "
        f"Hilos activos={current_proc.num_threads()}, "
        f"Estado SO={current_proc.status()}"
    )

    # ── FASE 1: Admisión de recursos (RAM) ─────────────────────────
    admitted = False
    retries  = 0

    while retries < MAX_RETRIES and not admitted:
        ram_lock.acquire()  # MUTEX: sección crítica sobre RAM compartida

        # ── INICIO SECCIÓN CRÍTICA ──────────────────────────────────
        # Aquí ocurriría una condición de carrera si dos hilos leen y
        # modifican ram_value.value al mismo tiempo sin el mutex.
        # Ejemplo de race condition sin mutex:
        #   Hilo A lee: ram = 800
        #   Hilo B lee: ram = 800   <- ambos ven 800, ambos creen que caben
        #   Hilo A escribe: ram = 1050  <- overflow!
        #   Hilo B escribe: ram = 1050  <- overflow!
        # El mutex evita esto: solo un hilo a la vez puede leer+escribir.
        if ram_value.value + proc.required_ram <= MAX_RAM_MB:
            ram_value.value += proc.required_ram
            with stats_lock:
                shared_stats["ram_current"] = ram_value.value
                if ram_value.value > shared_stats["ram_peak"]:
                    shared_stats["ram_peak"] = ram_value.value
            ram_lock.release()
            # ── FIN SECCIÓN CRÍTICA ─────────────────────────────────
            admitted = True
            log_event_thread(
                f"[PID {proc.pid}] RAM admitida: {proc.required_ram}MB "
                f"(total usado: {ram_value.value}MB)"
            )
        else:
            with stats_lock:
                shared_stats["total_waiting"] += 1
            ram_lock.release()
            proc.transition("WAITING", log_event_thread)
            log_event_thread(
                f"[PID {proc.pid}] WAITING por RAM. "
                f"Necesita {proc.required_ram}MB, disponible: "
                f"{MAX_RAM_MB - ram_value.value}MB (intento {retries + 1})"
            )
            retries += 1
            time.sleep(random.uniform(0.2, 0.6))
            proc.transition("READY", log_event_thread)

    if not admitted:
        proc.transition("TERMINATED", log_event_thread)
        proc.end_time = time.time()
        with stats_lock:
            shared_stats["total_rejected"] += 1
        log_event_thread(f"[PID {proc.pid}] RECHAZADO: sin recursos de RAM.")
        return

    # ── FASE 2: Ejecución Round-Robin ──────────────────────────────
    # El semáforo limita los hilos que ejecutan CPU simultáneamente
    worker_semaphore.acquire()  # Puede bloquear si hay MAX_WORKERS activos

    try:
        if proc.start_time is None:
            proc.start_time = time.time()

        proc.transition("RUNNING", log_event_thread)

        while proc.bursts_done < proc.cpu_bursts:
            log_event_thread(
                f"[PID {proc.pid}] RUNNING quantum {proc.bursts_done + 1}"
                f"/{proc.cpu_bursts} ({SCHEDULER_QUANTUM}s)"
            )
            time.sleep(SCHEDULER_QUANTUM)  # Simula uso de CPU
            proc.bursts_done += 1

            if proc.bursts_done < proc.cpu_bursts:
                # Preemption: el quantum expiró, vuelve a la cola
                log_event_thread(
                    f"[PID {proc.pid}] Quantum expirado -> preemption, vuelve a READY"
                )
                proc.transition("WAITING", log_event_thread)
                time.sleep(0.1)  # Simula cambio de contexto
                proc.transition("READY", log_event_thread)
                time.sleep(random.uniform(0.1, 0.3))
                proc.transition("RUNNING", log_event_thread)

        # Terminación exitosa
        proc.transition("TERMINATED", log_event_thread)
        proc.end_time = time.time()

        with stats_lock:
            shared_stats["total_processed"] += 1

        log_event_thread(
            f"[PID {proc.pid}] TERMINADO exitosamente. "
            f"Turnaround: {proc.turnaround_time()}s | "
            f"Espera: {proc.waiting_time()}s"
        )

    finally:
        # ── FASE 3: Liberación de recursos ─────────────────────────
        ram_lock.acquire()
        ram_value.value -= proc.required_ram
        with stats_lock:
            shared_stats["ram_current"] = ram_value.value
        ram_lock.release()

        worker_semaphore.release()
        log_event_thread(
            f"[PID {proc.pid}] {proc.required_ram}MB RAM liberados."
        )


# ============================================================
# BLOQUE 4: DEMOSTRACIÓN DE CONDICIÓN DE CARRERA (RACE CONDITION)
# ============================================================
# Este bloque demuestra pedagógicamente qué pasa cuando varios
# hilos acceden a una variable compartida SIN y CON mutex.
#
# CONDICIÓN DE CARRERA: ocurre cuando el resultado de un programa
# depende del orden no determinístico de ejecución de hilos.
#
# SECCIÓN CRÍTICA: fragmento de código donde se accede a recursos
# compartidos. Solo un hilo debe ejecutarla a la vez.
#
# MECANISMO DE SINCRONIZACIÓN: usamos threading.Lock() (mutex).
# Un mutex tiene dos estados: libre y ocupado.
#   - lock.acquire(): si está libre -> lo toma; si está ocupado -> espera.
#   - lock.release(): lo libera para que otro hilo pueda tomarlo.
# ============================================================

def demo_race_condition():
    """
    Muestra una condición de carrera SIN mutex y la solución CON mutex.
    Ambas funciones incrementan un contador 10,000 veces con 10 hilos.
    Sin mutex, el resultado es INCORRECTO. Con mutex, es siempre 100,000.
    """
    print("\n" + "=" * 60)
    print("  DEMOSTRACIÓN: CONDICIÓN DE CARRERA vs SINCRONIZACIÓN")
    print("=" * 60)

    # ── SIN MUTEX (condición de carrera) ───────────────────────────
    contador_sin_mutex = {"valor": 0}

    def incrementar_sin_mutex(n):
        for _ in range(n):
            # PROBLEMA: leer, sumar 1, escribir no es atómica.
            # Entre la lectura y la escritura, otro hilo puede cambiar el valor.
            temp = contador_sin_mutex["valor"]  # Leer
            time.sleep(0)                        # Yield (fuerza cambio de contexto)
            contador_sin_mutex["valor"] = temp + 1  # Escribir (puede estar desactualizado)

    hilos_sin = [threading.Thread(target=incrementar_sin_mutex, args=(1000,))
                 for _ in range(10)]
    for h in hilos_sin:
        h.start()
    for h in hilos_sin:
        h.join()

    resultado_sin = contador_sin_mutex["valor"]
    estado = "INCORRECTO (race condition)" if resultado_sin != 10000 else "correcto (tuviste suerte)"
    print(f"\n  Sin mutex  -> Esperado: 10,000 | Obtenido: {resultado_sin:,}  <- {estado}")

    # ── CON MUTEX (sincronización correcta) ────────────────────────
    contador_con_mutex = {"valor": 0}
    mutex = threading.Lock()

    def incrementar_con_mutex(n):
        for _ in range(n):
            mutex.acquire()        # Entra a la sección crítica
            # ── SECCIÓN CRÍTICA ─────────────────────────────────────
            # Solo un hilo puede estar aquí a la vez.
            # Leer + sumar + escribir ocurre de forma atómica.
            contador_con_mutex["valor"] += 1
            # ── FIN SECCIÓN CRÍTICA ──────────────────────────────────
            mutex.release()        # Sale de la sección crítica

    hilos_con = [threading.Thread(target=incrementar_con_mutex, args=(1000,))
                 for _ in range(10)]
    for h in hilos_con:
        h.start()
    for h in hilos_con:
        h.join()

    print(f"  Con mutex  -> Esperado: 10,000 | Obtenido: {contador_con_mutex['valor']:,}  <- CORRECTO\n")

    # ── DEMOSTRACIÓN DE SEMÁFORO ────────────────────────────────────
    # Un semáforo es como un mutex pero permite N accesos simultáneos.
    # Aquí limitamos a 2 hilos concurrentes sobre 6 tareas.
    print("  DEMOSTRACIÓN: SEMÁFORO (máx 2 concurrentes de 6 hilos)")
    sem = threading.Semaphore(2)
    active = {"count": 0}
    active_lock = threading.Lock()

    def tarea_semaforo(tid):
        sem.acquire()   # Si ya hay 2 activos, este hilo espera
        with active_lock:
            active["count"] += 1
            print(f"    Hilo {tid} ENTRANDO  -> {active['count']} activos simultáneos")
        time.sleep(0.5)
        with active_lock:
            active["count"] -= 1
            print(f"    Hilo {tid} SALIENDO  -> {active['count']} activos simultáneos")
        sem.release()

    hilos_sem = [threading.Thread(target=tarea_semaforo, args=(i,))
                 for i in range(1, 7)]
    for h in hilos_sem:
        h.start()
    for h in hilos_sem:
        h.join()
    print()


# ============================================================
# BLOQUE 5: MONITOREO REAL DE RECURSOS DEL SO (psutil)
# ============================================================
# Usamos psutil para leer métricas REALES del sistema:
#   - CPU: porcentaje de uso global
#   - Memoria: RAM física usada/disponible/total
#   - Hilos: cantidad de hilos del proceso actual en el SO
#   - Procesos: lista de procesos reales activos
# Estos datos vienen directamente de las APIs del SO
# (/proc en Linux, WMI en Windows, sysctl en macOS).
# ============================================================

def monitor_resources(stop_event, interval=1.5):
    """
    Hilo de monitoreo que cada 'interval' segundos lee y muestra
    métricas reales del sistema operativo.
    Corre mientras stop_event no esté activado.
    """
    print("\n  [MONITOR] Iniciando monitoreo de recursos del SO...")
    snapshots = []

    while not stop_event.is_set():
        proc         = psutil.Process(os.getpid())
        cpu_glob     = psutil.cpu_percent(interval=None)
        ram_info     = psutil.virtual_memory()
        num_threads  = proc.num_threads()
        ram_proc_mb  = proc.memory_info().rss / (1024 ** 2)

        snapshot = {
            "ts":        datetime.now().strftime("%H:%M:%S"),
            "cpu_pct":   cpu_glob,
            "ram_total": round(ram_info.total    / (1024 ** 2)),
            "ram_used":  round(ram_info.used      / (1024 ** 2)),
            "ram_avail": round(ram_info.available / (1024 ** 2)),
            "ram_pct":   ram_info.percent,
            "threads":   num_threads,
            "ram_proc":  round(ram_proc_mb, 1),
            "sim_ram":   shared_stats["ram_current"],
        }
        snapshots.append(snapshot)

        # Barra visual en consola
        bar_cpu = "█" * int(cpu_glob / 5) + "░" * (20 - int(cpu_glob / 5))
        bar_ram = "█" * int(ram_info.percent / 5) + "░" * (20 - int(ram_info.percent / 5))

        print(
            f"  [MONITOR {snapshot['ts']}] "
            f"CPU: [{bar_cpu}] {cpu_glob:5.1f}% | "
            f"RAM: [{bar_ram}] {snapshot['ram_pct']}% "
            f"({snapshot['ram_used']}MB/{snapshot['ram_total']}MB) | "
            f"Hilos SO: {num_threads} | "
            f"RAM simulada: {snapshot['sim_ram']}MB"
        )

        time.sleep(interval)

    return snapshots


def list_real_processes():
    """
    Lista los procesos reales del SO usando la API de psutil.
    Muestra PID, nombre, estado SO, CPU y RAM de cada proceso.
    """
    print("\n" + "=" * 60)
    print("  PROCESOS REALES DEL SISTEMA OPERATIVO (top 10 por RAM)")
    print("=" * 60)
    print(f"  {'PID':>7} {'NOMBRE':<25} {'ESTADO':<10} {'CPU%':>6} {'RAM MB':>8}")
    print("  " + "-" * 58)

    procs = []
    for p in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            ram  = info["memory_info"].rss / (1024 ** 2) if info["memory_info"] else 0
            procs.append((info["pid"], info["name"], info["status"],
                          info["cpu_percent"], ram))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    procs.sort(key=lambda x: x[4], reverse=True)

    for pid, name, status, cpu, ram in procs[:10]:
        print(f"  {pid:>7} {name:<25} {status:<10} {cpu:>6.1f} {ram:>8.1f}")

    current = psutil.Process(os.getpid())
    print(f"\n  -> Proceso ACTUAL: PID={current.pid} | "
          f"Hilos SO={current.num_threads()} | "
          f"Estado={current.status()} | "
          f"RAM={round(current.memory_info().rss / (1024 ** 2), 1)}MB")


# ============================================================
# BLOQUE 6: ORQUESTADOR PRINCIPAL (main)
# ============================================================
# Integra todos los bloques anteriores:
#   1. Muestra la demo de race condition y semáforos (pedagógico)
#   2. Lista procesos reales del SO
#   3. Lanza el hilo de monitoreo de recursos
#   4. Lee citas pendientes de la BD (de la Parte 1)
#   5. Crea un AppointmentProcess por cada cita
#   6. Lanza hilos reales para procesarlas concurrentemente
#   7. Espera a que todos terminen y muestra métricas finales
# ============================================================

def load_appointments_from_db(db_path="appointments.db", limit=6):
    """
    Lee citas de la base de datos creada en la Parte 1.
    Si no existe la BD, genera datos de prueba ficticios.
    """
    appointments = []

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur  = conn.cursor()
            cur.execute(
                "SELECT id, patient_name, doctor, appointment_date "
                "FROM appointments WHERE status='pending' LIMIT ?",
                (limit,)
            )
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                appointments.append({
                    "id":      row[0],
                    "patient": row[1],
                    "doctor":  row[2],
                    "date":    row[3],
                })
        except sqlite3.Error as e:
            print(f"  [WARN] Error leyendo BD: {e}. Usando datos de prueba.")

    # Si no hay datos reales, usar datos de prueba
    if not appointments:
        sample = [
            ("Ana López",     "Dr. García"),
            ("Juan Martínez", "Dra. Torres"),
            ("María Pérez",   "Dr. Ramírez"),
            ("Carlos Ruiz",   "Dra. Mendoza"),
            ("Laura Gómez",   "Dr. Castro"),
            ("Pedro Silva",   "Dra. Vargas"),
        ]
        for i, (pat, doc) in enumerate(sample[:limit], start=1):
            appointments.append({
                "id":      i,
                "patient": pat,
                "doctor":  doc,
                "date":    str(datetime.now().date()),
            })

    return appointments


def main():
    print("=" * 60)
    print("  SISTEMA DE CITAS MÉDICAS - PARTE 3")
    print("  Planificación, Concurrencia y Sincronización")
    print("=" * 60)

    # Limpiar log anterior
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    # ── PASO 1: Demostración académica (race condition + semáforo) ──
    demo_race_condition()

    # ── PASO 2: Listar procesos reales del SO ──────────────────────
    list_real_processes()

    # ── PASO 3: Variables compartidas (igual que Parte 2) ──────────
    # Usamos multiprocessing.Value para RAM compartida entre hilos/procesos
    ram_value = Value("i", 0)
    ram_lock  = MpLock()

    # ── PASO 4: Hilo de monitoreo de recursos ──────────────────────
    stop_monitor      = threading.Event()
    monitor_snapshots = []

    def monitor_wrapper():
        nonlocal monitor_snapshots
        monitor_snapshots = monitor_resources(stop_monitor, interval=1.5)

    monitor_thread = threading.Thread(
        target=monitor_wrapper,
        name="Monitor-Recursos",
        daemon=True   # Muere automáticamente si el proceso principal termina
    )
    monitor_thread.start()

    # ── PASO 5: Cargar citas y crear procesos ──────────────────────
    print("\n" + "=" * 60)
    print("  EJECUTANDO PLANIFICADOR ROUND-ROBIN CON HILOS REALES")
    print("=" * 60)

    appointments = load_appointments_from_db(limit=6)
    scheduler    = RoundRobinScheduler(quantum=SCHEDULER_QUANTUM)
    workers      = []

    for idx, appt in enumerate(appointments, start=1):
        priority = random.choice([1, 2, 3])
        proc = AppointmentProcess(
            pid            = idx,
            appointment_id = appt["id"],
            patient_name   = appt["patient"],
            doctor         = appt["doctor"],
            priority       = priority,
        )

        # NEW -> READY
        proc.transition("READY", log_event_thread)
        scheduler.enqueue(proc)

        # Lanzar hilo real del SO para esta cita
        t = threading.Thread(
            target  = worker_thread,
            args    = (proc, scheduler, ram_value, ram_lock),
            name    = f"Cita-{idx}-{appt['patient'].split()[0]}",
            daemon  = False
        )
        workers.append((t, proc))

    # Iniciar todos los hilos (concurrencia observable)
    print(f"\n  Lanzando {len(workers)} hilos concurrentes "
          f"(semáforo: máx {MAX_WORKERS} simultáneos)...\n")

    start_time = time.time()

    for t, _ in workers:
        t.start()
        time.sleep(0.05)  # Escalonar arranque para visualizar mejor el log

    # Esperar a que todos los hilos terminen
    for t, _ in workers:
        t.join()

    total_time = round(time.time() - start_time, 2)

    # ── PASO 6: Detener monitor y mostrar métricas finales ─────────
    stop_monitor.set()
    monitor_thread.join(timeout=3)

    print("\n" + "=" * 60)
    print("  MÉTRICAS FINALES DEL SISTEMA")
    print("=" * 60)
    print(f"  Tiempo total de ejecución  : {total_time}s")
    print(f"  Citas procesadas (éxito)   : {shared_stats['total_processed']}")
    print(f"  Citas rechazadas (OOM)     : {shared_stats['total_rejected']}")
    print(f"  Eventos de espera (WAITING): {shared_stats['total_waiting']}")
    print(f"  RAM simulada pico          : {shared_stats['ram_peak']}MB / {MAX_RAM_MB}MB")
    print(f"  Quantum Round-Robin        : {SCHEDULER_QUANTUM}s")
    print(f"  Semáforo (max workers)     : {MAX_WORKERS}")

    print(f"\n  {'PID':>4} {'PACIENTE':<18} {'ESTADO':<12} "
          f"{'TURNAROUND':>12} {'ESPERA':>8} {'RAM MB':>8}")
    print("  " + "-" * 64)

    for _, proc in workers:
        ta = f"{proc.turnaround_time()}s" if proc.turnaround_time() else "N/A"
        wt = f"{proc.waiting_time()}s"    if proc.waiting_time()    else "N/A"
        print(f"  {proc.pid:>4} {proc.patient_name:<18} {proc.state:<12} "
              f"{ta:>12} {wt:>8} {proc.required_ram:>8}")

    print(f"\n  Log completo guardado en: {LOG_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()

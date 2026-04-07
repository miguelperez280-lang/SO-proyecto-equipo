import os
import time
import random
from multiprocessing import Process, Value, Lock

MAX_RAM = 1000  
MAX_RETRIES = 3 

def log_event(message, log_lock):
    with log_lock:
        # Abrir archivo síncronamente (O_WRONLY, O_CREAT, O_APPEND)
        fd = os.open("run.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        log_line = f"[{time.strftime('%H:%M:%S')}] {message}\n".encode('utf-8')
        os.write(fd, log_line) # Escritura directa
        os.close(fd)           # Cierre síncrono

def process_appointment(pid, current_ram, max_ram, wait_events, mem_lock, log_lock):
    """
    Simula el hilo/proceso que gestiona la cita médica.
    """
    required_ram = random.randint(200, 500) # Memoria requerida para esta cita
    retries = 0
    admitted = False

    # 3) Integración con Unidad 2: Control de admisión, espera y rechazo
    while retries < MAX_RETRIES:
        
        # 2) Sincronización: Mutex (Lock) sobre memoria compartida (Value)
        mem_lock.acquire()
        
        if current_ram.value + required_ram <= MAX_RAM:
            # Admisión exitosa
            current_ram.value += required_ram
            
            # Registrar métrica de RAM máxima
            if current_ram.value > max_ram.value:
                max_ram.value = current_ram.value
                
            mem_lock.release()
            admitted = True
            break
        else:
            # Evento de espera
            wait_events.value += 1
            mem_lock.release()
            log_event(f"Worker {pid}: ESPERA de memoria. Requiere {required_ram}MB", log_lock)
            retries += 1
            time.sleep(random.uniform(0.1, 0.5)) # Simula espera activa/bloqueo

    # Rechazo por falta de memoria
    if not admitted:
        log_event(f"Worker {pid}: RECHAZADO (Out of Memory). Requiere {required_ram}MB", log_lock)
        return

    # Si fue admitido, procesamos la cita (simulado)
    log_event(f"Worker {pid}: ADMITIDO. Procesando cita con {required_ram}MB", log_lock)
    time.sleep(random.uniform(0.5, 1.5)) 

    # Liberación de memoria
    mem_lock.acquire()
    current_ram.value -= required_ram
    mem_lock.release()
    log_event(f"Worker {pid}: FINALIZADO. {required_ram}MB liberados.", log_lock)

if __name__ == '__main__':
    # Preparación del entorno
    if os.path.exists("run.log"):
        os.remove("run.log")

    # Variables de Memoria Compartida (Equivalente a SharedArrayBuffer + Atomics)
    # 'i' indica que son enteros (integers) a nivel de C
    current_ram = Value('i', 0)
    max_ram = Value('i', 0)
    wait_events = Value('i', 0)
    
    # Mutexes para sincronización
    mem_lock = Lock()
    log_lock = Lock()

    print("Iniciando Sistema Operativo de Citas Médicas...")
    print(f"Capacidad Máxima de RAM: {MAX_RAM} MB\n")

    # 1) Concurrencia real demostrable: multiprocessing levanta procesos reales
    # en el SO, distribuyéndose en múltiples núcleos del procesador.
    workers = []
    for i in range(1, 6): # Generamos 10 citas simultáneas
        p = Process(target=process_appointment, 
                    args=(i, current_ram, max_ram, wait_events, mem_lock, log_lock))
        workers.append(p)
        p.start()

    # Esperamos a que todos los workers terminen
    for p in workers:
        p.join()

    # 3) Métricas finales
    print("--- MÉTRICAS DEL SISTEMA ---")
    print(f"RAM Máxima Utilizada: {max_ram.value} MB")
    print(f"Total de Eventos de Espera: {wait_events.value}")
    print("El archivo 'run.log' ha sido escrito exitosamente usando llamadas del SO.")

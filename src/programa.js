// Simulación de Gestión de Procesos: Round Robin (Quantum = 2)

const quantum = 2;
let tiempoActual = 0;
let totalRetorno = 0;
let totalEspera = 0;

// Definición de los procesos (asumimos que llegan en t=0)
const procesos = [
    { id: 'P1', burstOriginal: 5, burstRestante: 5, completado: false },
    { id: 'P2', burstOriginal: 3, burstRestante: 3, completado: false },
    { id: 'P3', burstOriginal: 1, burstRestante: 1, completado: false },
    { id: 'P4', burstOriginal: 2, burstRestante: 2, completado: false }
];

const cola = [...procesos];//se colocan puntos suspensivos para que no se modifique el arreglo original, y dentro de [] para crear un arreglo indentico
const resultados = [];

// Bucle principal de la simulación
while (cola.length > 0) {
    let proceso = cola.shift(); //saca el primer elemento del arreglo

    // Ejecutar por el quantum o por el tiempo restante, lo que sea menor
    let tiempoEjecucion = Math.min(proceso.burstRestante, quantum);
    proceso.burstRestante -= tiempoEjecucion; // se resta el burst restante por el tiempo de ejecucion
    tiempoActual += tiempoEjecucion;

    if (proceso.burstRestante > 0) {
        // El proceso no ha terminado, vuelve a la cola
        cola.push(proceso);
    } else {
        // El proceso ha terminado y se guardan sus datos
        proceso.completado = true;
        proceso.tiempoFinalizacion = tiempoActual;
        proceso.tiempoRetorno = proceso.tiempoFinalizacion - 0; // Llegada en 0
        proceso.tiempoEspera = proceso.tiempoRetorno - proceso.burstOriginal;

        totalRetorno += proceso.tiempoRetorno;
        totalEspera += proceso.tiempoEspera;
        resultados.push(proceso);
    }
}

// Ordenar resultados para la salida (P1, P2, P3)
resultados.sort((a, b) => a.id.localeCompare(b.id));

// Formateo de la salida
console.log('\n');
resultados.forEach(p => {
    console.log(`${p.id}: Burst=${p.burstOriginal}s, `);
    console.log(`T.Retorno=${p.tiempoRetorno}, `);
    console.log(`T.Espera=${p.tiempoEspera}\n`);
});

const promRetorno = (totalRetorno / resultados.length);
const promEspera = (totalEspera / resultados.length);

console.log('\n');
console.log(`Promedio T.Retorno: ${promRetorno}s\n`);
console.log(`Promedio T.Espera: ${promEspera}s\n`);
console.log('Presione ENTER para salir...█\n');
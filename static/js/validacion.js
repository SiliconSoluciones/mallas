document.addEventListener('DOMContentLoaded', () => {
    // Validación de fecha
    const fechaInputs = document.querySelectorAll('input[type="date"]');
    
    fechaInputs.forEach(input => {
        input.addEventListener('change', function() {
            const year = this.value.split('-')[0];
            if (year < 2000 || year > 2100) {
                Swal.fire({
                    icon: 'error',
                    title: 'Año inválido',
                    text: 'El año debe estar entre 2000 y 2100',
                });
                this.value = '';
            }
        });
    });

    // Validación de teléfono (solo números)
    const telInputs = document.querySelectorAll('input[type="tel"]');
    
    telInputs.forEach(input => {
        input.addEventListener('input', function() {
            this.value = this.value.replace(/[^0-9]/g, '');
        });
    });
});
/** Never infer controller creation from file permissions alone. */
export function controllerMessage(state?: string): string {
  switch (state) {
    case 'device_missing': return 'Input device missing — restart the box. If it stays missing, check the uinput module and startup configuration.';
    case 'access_denied': return 'Input access denied — the Couchside service cannot open the input device. Check its input-group access and device permissions.';
    case 'creation_failed': return 'Controller creation failed on the last attempt — device access works now. Reconnect the phone and try controller mode; if it fails again, check the service log.';
    case 'service_stopped': return 'Couchside service is stopped — start it before checking the controller.';
    case 'accessible': return 'Input device accessible — a controller is created when a phone connects in controller mode.';
    default: return 'Controller status unknown — the service could not report the cause. Check its version and service log; reinstalling is not required just to diagnose this.';
  }
}

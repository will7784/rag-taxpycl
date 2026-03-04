angular.module('acj', ['ui.bootstrap', 'ui.router','ngRoute', 'ngAnimate','ngSanitize', 'textAngular','comun','angularFileUpload']);

angular.module('acj').config(function($stateProvider, $urlRouterProvider) {

    /* Add New States Above */
	$urlRouterProvider.otherwise('/instancia/1');

});

angular.module('acj').run(function($rootScope) {

    $rootScope.safeApply = function(fn) {
        var phase = $rootScope.$$phase;
        if (phase === '$apply' || phase === '$digest') {
            if (fn && (typeof(fn) === 'function')) {
                fn();
            }
        } else {
            this.$apply(fn);
        }
    };

});


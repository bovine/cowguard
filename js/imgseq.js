function animateNextFrame () {
    $('#imagePlayer').attr('src', frameAry[nextFrame].attr('src'));
    $("#sliderbar").slider({ value: nextFrame+1 });
    nextFrame = (nextFrame + 1) % numFrames;
}

function animationStop () {
    if (animTimer != 0) {
        clearInterval(animTimer);
        animTimer = 0;
    }
}

function animationStart() {
    if (numFrames > 0 && animTimer == 0) {
        animTimer = setInterval("animateNextFrame()", 500);
    }
}

var animTimer = 0;
animationStart();

$(function() {
	$("#sliderbar").slider({
            animate: true,
			value: 1,
			min: 1,
			max: numFrames,
			step: 1,
            start: function(event, ui) {
                // stop playback
                animationStop();
            },
			slide: function(event, ui) {
                nextFrame = ui.value - 1;
                animateNextFrame();
			}
		});
        

    $("#playback_first_frame")
        .button( { text: false, icons: { primary: "ui-icon-seek-first" }})
        .click( function() {
            animationStop();
            nextFrame = 0;
            animateNextFrame();
        });
    $("#playback_prev_frame")
        .button( { text: false, icons: { primary: "ui-icon-seek-prev" }})
        .click( function() {
            animationStop();
            nextFrame = (nextFrame + numFrames - 2) % numFrames;
            animateNextFrame();
        });
    $("#playback_stopstart")
        .button( { text: false, icons: { primary: "ui-icon-play", secondary: "ui-icon-pause" } })
        .click( function() {
            if (animTimer != 0) {
                animationStop();
            } else {
                animationStart();
            }
        });
    $("#playback_next_frame")
        .button( { text: false, icons: { primary: "ui-icon-seek-next" }})
        .click( function() {
            animationStop();
            animateNextFrame();
        });
    $("#playback_last_frame")
        .button( { text: false, icons: { primary: "ui-icon-seek-end" }})
        .click( function() {
            animationStop();
            nextFrame = numFrames - 1;
            animateNextFrame();
        });

    $("#playback_group").buttonset();
});

function handle_back_button() {
    //document.location = "";
    //location.replace("xxx");
    // TODO: if the previous location is not what we expect, then go to the intended URL.
    history.go(-1);
}

function handle_delete_button(eventkey) {
    animationStop();
    $.post('/events/delete', { event: eventkey }, function(data) { 
        alert(data); 
        //location.reload(); 
        history.go(-1);
        // TODO: force refresh of previous page.
        // window.location.reload(history.go(-2));
    });
}
